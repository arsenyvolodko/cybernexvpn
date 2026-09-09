"""Сервисный слой подписки: начисление дней, покупка периода, смена тарифа.

Единственное место, где меняется `Subscription.expires_at`. Любое изменение
обязано пройти через `grant_days`, чтобы в `SubscriptionEvent` осталась запись —
иначе потом невозможно объяснить, откуда у человека взялись дни.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import now

from nexvpn.enums import PanelSyncStatusEnum, SubscriptionEventReasonEnum
from nexvpn.models import (
    NexUser,
    Payment,
    Plan,
    SentReminder,
    Subscription,
    SubscriptionEvent,
    UserInvitation,
)
from nexvpn.subscription import pricing

logger = logging.getLogger(__name__)


class SubscriptionError(Exception):
    """Операция над подпиской невозможна в текущем состоянии."""


def normalize_expiry(moment):
    """Округлить окончание подписки вверх до фиксированного часа.

    Без этого подписка кончается в тот же час суток, когда её оплатили, — и
    напоминание «за час» может прилететь в четыре утра. Округляем **вверх**:
    человек получит несколько часов сверху, но не потеряет ни минуты.
    """
    local = timezone.localtime(moment)
    target = local.replace(
        hour=settings.SUBSCRIPTION_EXPIRY_HOUR, minute=0, second=0, microsecond=0
    )
    if target < local:
        target += timedelta(days=1)
    return target


def get_plan(device_limit: int) -> Plan:
    return Plan.objects.get(device_limit=device_limit)


def trial_plan() -> Plan:
    return get_plan(settings.TRIAL_PLAN_DEVICES)


@transaction.atomic
def grant_days(
    user: NexUser,
    days: int,
    reason: SubscriptionEventReasonEnum | str,
    plan: Plan | None = None,
    amount: int = 0,
    payment: Payment | None = None,
    comment: str = "",
) -> Subscription:
    """Начислить дни. Если подписки нет — создать её на тарифе `plan`.

    Отсчёт идёт от `max(now, expires_at)`: у активной подписки дни добавляются
    в хвост, у истёкшей — начинаются заново с текущего момента (сгоревшее время
    не возвращается).
    """
    if days < 0:
        raise ValueError("grant_days ждёт неотрицательное число дней")

    subscription = Subscription.objects.select_for_update().filter(user=user).first()

    if subscription is None:
        if plan is None:
            raise SubscriptionError("Нельзя создать подписку без тарифа")
        expires_before = None
        subscription = Subscription(user=user, plan=plan, expires_at=now())
    else:
        expires_before = subscription.expires_at
        if plan is not None and plan != subscription.plan:
            raise SubscriptionError(
                "grant_days не меняет тариф — для этого есть change_plan"
            )
        plan = subscription.plan

    base = max(now(), subscription.expires_at)
    subscription.expires_at = normalize_expiry(base + timedelta(days=days))
    subscription.panel_status = PanelSyncStatusEnum.PENDING
    subscription.save()

    # Период сдвинулся — напоминания по нему надо слать заново.
    SentReminder.objects.filter(subscription=subscription).delete()

    SubscriptionEvent.objects.create(
        user=user,
        subscription=subscription,
        reason=reason,
        delta_days=days,
        plan=plan,
        price_month=plan.price_month,
        amount=amount,
        payment=payment,
        expires_at_before=expires_before,
        expires_at_after=subscription.expires_at,
        comment=comment,
    )
    return subscription


def trial_available(user: NexUser) -> bool:
    """Положен ли человеку пробный и не начат ли он уже.

    Отдельно от `grant_trial`, потому что спрашивают об этом чаще, чем выдают:
    экран подписки должен показать «три дня ждут тебя» тому, кто ещё не
    подключался, и не соврать тому, кому пробный не полагается.
    """
    if user.is_legacy:
        return False
    return not Subscription.objects.filter(user=user).exists()


def grant_trial(user: NexUser) -> Subscription | None:
    """Пробный период по-настоящему новому пользователю.

    Выдаётся **в момент нажатия «Подключиться»**, а не при первом заходе в
    бота. Иначе отсчёт начинался раньше, чем человек успевал подписаться на
    канал: на 09.09.2026 из 36 выданных пробных 14 не были использованы ни
    разу, и у 12 из них три дня уже сгорели впустую.

    Привязать выдачу к первому подключению устройства нельзя: чтобы
    подключиться, нужна рабочая ссылка подписки, а панель отдаёт конфиги
    только при действующем сроке. Ближайший к намерению момент, в который
    ссылку всё равно приходится выдавать, — это и есть «Подключиться».

    Легаси-пользователи пробного не получают: у них уже есть перенесённые дни,
    а выдавать бонус «за новизну» тому, кто просто зашёл в обновлённого бота,
    мы не собирались.
    """
    if not trial_available(user):
        return None
    return grant_days(
        user,
        days=settings.TRIAL_DAYS,
        reason=SubscriptionEventReasonEnum.TRIAL,
        plan=trial_plan(),
    )


@transaction.atomic
def purchase_period(
    user: NexUser,
    plan: Plan,
    amount: int,
    payment: Payment | None = None,
    months: int = 1,
) -> Subscription:
    """Оплаченный срок на тарифе `plan`: `months` × 30 дней.

    Если подписка истекла и был запланирован переход на меньший тариф — он
    считается состоявшимся ровно здесь: новый период начинается уже на нём.
    """
    subscription = Subscription.objects.select_for_update().filter(user=user).first()
    if subscription is not None:
        subscription = ensure_current_plan(subscription)

    if subscription is not None and subscription.plan_id != plan.pk:
        if subscription.is_active:
            raise SubscriptionError(
                "У активной подписки другой тариф — сначала change_plan, потом оплата"
            )
        # Подписка истекла: сменить тариф при покупке нового периода можно свободно.
        subscription.plan = plan
        subscription.next_plan = None
        subscription.save(update_fields=["plan", "next_plan", "updated_at"])
    elif subscription is not None and subscription.next_plan_id == plan.pk:
        subscription.next_plan = None
        subscription.save(update_fields=["next_plan", "updated_at"])

    subscription = grant_days(
        user,
        days=pricing.days_in_period() * months,
        reason=SubscriptionEventReasonEnum.PURCHASE,
        plan=plan if subscription is None else None,
        amount=amount,
        payment=payment,
        comment=f"{months} мес." if months > 1 else "",
    )
    grant_referral_rewards_if_first_payment(user)
    return subscription


def quote_plan_change(user: NexUser, new_plan: Plan) -> pricing.PlanChangeQuote:
    subscription = Subscription.objects.filter(user=user).select_related("plan").first()
    if subscription is None:
        raise SubscriptionError("У пользователя нет подписки")
    subscription = ensure_current_plan(subscription)
    return pricing.quote_plan_change(
        days_left=subscription.days_left,
        price_from=subscription.plan.price_month,
        price_to=new_plan.price_month,
    )


@transaction.atomic
def change_plan_now(
    user: NexUser,
    new_plan: Plan,
    amount_paid: int = 0,
    payment: Payment | None = None,
) -> Subscription:
    """Немедленный переход на другой тариф с сохранением стоимости остатка.

    Остаток пересчитывается по цене нового тарифа (30 дней по 150₽ → 11 дней
    по 400₽), и сверху добавляется полный период, если пользователь доплатил.
    Ни при каком размере остатка деньги не сгорают.
    """
    subscription = Subscription.objects.select_for_update().select_related("plan").filter(user=user).first()
    if subscription is None:
        raise SubscriptionError("У пользователя нет подписки")
    if subscription.plan_id == new_plan.pk:
        raise SubscriptionError("Это уже текущий тариф")

    old_plan = subscription.plan
    days_left = subscription.days_left
    quote = pricing.quote_plan_change(days_left, old_plan.price_month, new_plan.price_month)

    converted_days = quote.converted_days
    if amount_paid > 0:
        if quote.topup_price is None:
            raise SubscriptionError(
                "Доплата при таком остатке невыгодна пользователю — переход должен быть бесплатным"
            )
        if amount_paid < quote.topup_price:
            raise SubscriptionError(
                f"Недостаточная доплата: нужно {quote.topup_price}₽, получено {amount_paid}₽"
            )
        converted_days = quote.topup_days

    expires_before = subscription.expires_at
    subscription.plan = new_plan
    subscription.next_plan = None
    if converted_days > 0:
        subscription.expires_at = normalize_expiry(now() + timedelta(days=converted_days))
    else:
        # Ноль дней после пересчёта — доступа нет, и срок не должен уехать
        # вперёд. `normalize_expiry` округляет вверх до 20:00, поэтому
        # `normalize_expiry(now())` всегда попадает в будущее: у истёкшей
        # подписки это дарило до суток и **оживляло** её, а меняя тариф
        # туда-обратно каждый вечер, можно было продлевать доступ бесконечно.
        #
        # Уже истёкшая остаётся истёкшей, а живая, чей остаток обнулился при
        # пересчёте, кончается сейчас — ровно то, что означает «ноль дней».
        subscription.expires_at = min(now(), expires_before)
    subscription.panel_status = PanelSyncStatusEnum.PENDING
    subscription.save()
    SentReminder.objects.filter(subscription=subscription).delete()

    is_upgrade = new_plan.price_month > old_plan.price_month
    SubscriptionEvent.objects.create(
        user=user,
        subscription=subscription,
        reason=(
            SubscriptionEventReasonEnum.PLAN_UPGRADE
            if is_upgrade
            else SubscriptionEventReasonEnum.PLAN_DOWNGRADE_APPLIED
        ),
        delta_days=converted_days - days_left,
        plan=new_plan,
        price_month=new_plan.price_month,
        amount=amount_paid,
        payment=payment,
        expires_at_before=expires_before,
        expires_at_after=subscription.expires_at,
        comment=f"{old_plan.device_limit} → {new_plan.device_limit} устр., остаток {days_left} → {converted_days} дн.",
    )

    if amount_paid > 0:
        grant_referral_rewards_if_first_payment(user)
    return subscription


@transaction.atomic
def schedule_plan_downgrade(user: NexUser, new_plan: Plan) -> Subscription:
    """Переход на меньший тариф с начала следующего периода.

    До конца оплаченного периода у человека остаются все устройства, которые он
    оплатил, — забирать их досрочно было бы нечестно.
    """
    subscription = Subscription.objects.select_for_update().select_related("plan").filter(user=user).first()
    if subscription is None:
        raise SubscriptionError("У пользователя нет подписки")
    if new_plan.price_month >= subscription.plan.price_month:
        raise SubscriptionError("Это не понижение тарифа — используйте change_plan_now")

    subscription.next_plan = new_plan
    subscription.save(update_fields=["next_plan", "updated_at"])

    SubscriptionEvent.objects.create(
        user=user,
        subscription=subscription,
        reason=SubscriptionEventReasonEnum.PLAN_DOWNGRADE_SCHEDULED,
        delta_days=0,
        plan=new_plan,
        price_month=new_plan.price_month,
        expires_at_before=subscription.expires_at,
        expires_at_after=subscription.expires_at,
        comment=f"С {subscription.expires_at:%d.%m.%Y}: {subscription.plan.device_limit} → {new_plan.device_limit} устр.",
    )
    return subscription


def ensure_current_plan(subscription: Subscription) -> Subscription:
    """Применить отложенное понижение, если оплаченный период уже кончился.

    Вызывается лениво — из тех мест, где подписку читают или меняют. Отдельный
    планировщик для этого не нужен: пока подписка истёкшая, доступа всё равно
    нет (панель режет по expireAt), и лимит устройств ни на что не влияет.
    Важно только, чтобы к моменту следующего показа или оплаты тариф был
    актуальным — а этого ленивого вызова достаточно.
    """
    if subscription.next_plan_id is None or subscription.is_active:
        return subscription
    return apply_scheduled_downgrade(subscription)


def cancel_scheduled_downgrade(user: NexUser) -> Subscription:
    """Передумал понижать тариф — снимаем запланированный переход."""
    subscription = Subscription.objects.filter(user=user).first()
    if subscription is None:
        raise SubscriptionError("У пользователя нет подписки")
    if subscription.next_plan_id is not None:
        subscription.next_plan = None
        subscription.save(update_fields=["next_plan", "updated_at"])
    return subscription


@transaction.atomic
def apply_scheduled_downgrade(subscription: Subscription) -> Subscription:
    """Применить отложенное понижение тарифа. Вызывается по истечении периода.

    Лишние устройства сносятся здесь же, автоматически: панель их сама не
    выбрасывает, а спросить человека в этот момент нельзя — переход случается
    сам по себе, без его участия. Уходят те, которыми дольше всего не
    пользовались; предупреждение об этом человек видел, когда выбирал тариф,
    и в напоминаниях за последние сутки.
    """
    if subscription.next_plan_id is None:
        return subscription

    old_plan = subscription.plan
    subscription.plan = subscription.next_plan
    subscription.next_plan = None
    subscription.panel_status = PanelSyncStatusEnum.PENDING
    subscription.save(update_fields=["plan", "next_plan", "panel_status", "updated_at"])

    removed = []
    try:
        from nexvpn.subscription import panel_sync

        removed = panel_sync.trim_devices_to_limit(subscription)
    except Exception:
        # Панель молчит — тариф всё равно сменился, а устройства подчистит
        # следующий вызов. Ронять переход из-за этого нельзя.
        logger.warning("Не удалось подчистить устройства после понижения %s",
                       subscription.pk, exc_info=True)

    SubscriptionEvent.objects.create(
        user=subscription.user,
        subscription=subscription,
        reason=SubscriptionEventReasonEnum.PLAN_DOWNGRADE_APPLIED,
        delta_days=0,
        plan=subscription.plan,
        price_month=subscription.plan.price_month,
        expires_at_before=subscription.expires_at,
        expires_at_after=subscription.expires_at,
        comment=(
            f"{old_plan.device_limit} → {subscription.plan.device_limit} устр."
            + (f", удалено лишних: {len(removed)}" if removed else "")
        ),
    )
    return subscription


# --- реферальная программа ---------------------------------------------


def register_invitation(inviter: NexUser, invitee: NexUser) -> UserInvitation:
    """Переход по реферальной ссылке. Бонусы — только после первой оплаты."""
    if inviter.pk == invitee.pk:
        raise SubscriptionError("Нельзя пригласить самого себя")
    if invitee.is_legacy:
        raise SubscriptionError("Пользователь из старой базы не может быть приглашённым")
    if UserInvitation.objects.filter(invitee=invitee).exists():
        raise SubscriptionError("Пользователь уже пришёл по чьей-то ссылке")
    return UserInvitation.objects.create(inviter=inviter, invitee=invitee)


@transaction.atomic
def grant_referral_rewards_if_first_payment(invitee: NexUser) -> bool:
    """Первая оплата приглашённого: бонус обоим.

    Инвайтеру — фиксированные дни на его текущем тарифе, приглашённому — дни на
    том тарифе, который он только что оплатил. `reward_granted_at` гарантирует,
    что это случится ровно один раз.
    """
    invitation = (
        UserInvitation.objects.select_for_update()
        .filter(invitee=invitee, reward_granted_at__isnull=True)
        .select_related("inviter")
        .first()
    )
    if invitation is None:
        return False

    invitation.reward_granted_at = now()
    invitation.save(update_fields=["reward_granted_at"])

    grant_days(
        invitee,
        days=settings.REFERRAL_INVITEE_DAYS,
        reason=SubscriptionEventReasonEnum.REFERRAL_INVITEE,
        comment=f"Первая оплата по приглашению от {invitation.inviter}",
    )

    inviter = invitation.inviter
    inviter_subscription = Subscription.objects.filter(user=inviter).select_related("plan").first()
    grant_days(
        inviter,
        days=settings.REFERRAL_INVITER_DAYS,
        reason=SubscriptionEventReasonEnum.REFERRAL_INVITER,
        # У инвайтера без подписки бонус открывает её на тарифе пробного периода.
        plan=None if inviter_subscription else trial_plan(),
        comment=f"Первая оплата приглашённого {invitee}",
    )
    return True
