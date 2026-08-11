"""Мост между aiogram и синхронным сервисным слоем биллинга.

Бизнес-логика живёт в `nexvpn.subscription.service` и намеренно синхронная:
там `transaction.atomic` и `select_for_update`, на которых держится
корректность денег. Здесь только обёртки `sync_to_async` — ни одного правила
дублировать нельзя, иначе бот и веб начнут считать по-разному.
"""

import hashlib
import logging
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.timezone import now

from nexvpn import payments
from nexvpn.enums import PaymentKindEnum, SubscriptionEventReasonEnum
from nexvpn.models import (
    BillingPeriod,
    DeviceConnectionWatch,
    GlobalSettings,
    NexUser,
    Plan,
    Subscription,
    SubscriptionEvent,
    UserInvitation,
)
from nexvpn.remnawave import RemnawaveError
from nexvpn.subscription import panel_sync, pricing, service
from nexvpn.subscription.service import SubscriptionError

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionView:
    """Всё, что нужно экрану подписки, одним запросом."""

    subscription: Subscription | None
    devices_used: int | None  # None — панель не ответила
    device_limit: int
    web_url: str | None

    @property
    def exists(self) -> bool:
        return self.subscription is not None

    @property
    def is_active(self) -> bool:
        return self.subscription is not None and self.subscription.is_active

    @property
    def can_add_device(self) -> bool:
        # Панель молчит — не запрещаем: пусть лучше человек упрётся в честный
        # отказ панели, чем мы спрячем кнопку из-за сетевого сбоя.
        if self.devices_used is None:
            return True
        return self.devices_used < self.device_limit


@sync_to_async
def get_or_create_user(telegram_id: int, username: str | None, first_name: str | None) -> tuple[NexUser, bool]:
    """Пользователь бота. Новому сразу выдаётся пробный период.

    Легаси-пользователю пробный не полагается — это решает `grant_trial`,
    здесь только отмечаем факт первого захода в новую версию.
    """
    user, created = NexUser.objects.get_or_create(
        pk=telegram_id,
        defaults={"username": username, "first_name": first_name},
    )

    fields_to_update = []
    if username and user.username != username:
        user.username = username
        fields_to_update.append("username")
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        fields_to_update.append("first_name")
    if user.activated_at is None:
        user.activated_at = now()
        fields_to_update.append("activated_at")
    if fields_to_update:
        user.save(update_fields=fields_to_update)

    if created:
        subscription = service.grant_trial(user)
        if subscription is not None:
            _sync_quietly(subscription)

    return user, created


@sync_to_async
def mark_joined_channel(user: NexUser) -> None:
    user.joined_channel = True
    user.save(update_fields=["joined_channel"])


@sync_to_async
def was_invited(user: NexUser) -> bool:
    """Пришёл ли человек по чужой ссылке — от этого зависит абзац в приветствии."""
    return UserInvitation.objects.filter(invitee=user).exists()


@sync_to_async
def get_trial_plan() -> Plan | None:
    """Тариф пробного периода — нужен приветствию, чтобы назвать его словами."""
    return service.trial_plan()


@sync_to_async
def register_referral(user: NexUser, payload: str) -> bool:
    """Переход по реферальной ссылке: в payload лежит id пригласившего.

    Возвращает, засчиталось ли приглашение. Отказов много и все штатные —
    пришёл по своей же ссылке, уже приходил по чужой, легаси-пользователь,
    мусор в параметре. Ни один из них не повод показывать человеку ошибку:
    он просто увидит обычное приветствие.
    """
    if not payload.isdigit():
        return False
    inviter = NexUser.objects.filter(pk=int(payload)).first()
    if inviter is None:
        return False
    try:
        service.register_invitation(inviter, user)
    except SubscriptionError as exc:
        logger.info("Приглашение %s → %s не засчитано: %s", inviter.pk, user.pk, exc)
        return False
    return True


@sync_to_async
def get_subscription_view(user: NexUser) -> SubscriptionView:
    subscription = (
        Subscription.objects.select_related("plan", "next_plan", "user")
        .filter(user=user)
        .first()
    )
    if subscription is None:
        return SubscriptionView(subscription=None, devices_used=None, device_limit=0, web_url=None)

    subscription = service.ensure_current_plan(subscription)

    devices_used = None
    try:
        devices_used = len(panel_sync.list_devices(subscription))
    except RemnawaveError as exc:
        logger.warning("Панель не отдала устройства для %s: %s", user.pk, exc)

    return SubscriptionView(
        subscription=subscription,
        devices_used=devices_used,
        device_limit=subscription.device_limit,
        web_url=subscription.subscription_url,
    )


@dataclass
class Device:
    """Устройство из панели, приведённое к тому, что показываем человеку."""

    hwid: str
    token: str  # короткий стабильный ключ для callback_data
    title: str
    platform: str | None
    last_seen: str | None


@dataclass
class ReferralView:
    link: str
    invited: int
    pending: int
    rewarded: int
    days_earned: int
    history: list[tuple[str, int, str]]  # дата, дни, за кого


def _device_token(hwid: str) -> str:
    """HWID до 64 символов, а в callback_data влезает 64 байта на всё сообщение.

    Кладём короткий детерминированный хеш и сопоставляем его с настоящим HWID
    уже после повторного запроса списка. Индекс в списке был бы опаснее: между
    отрисовкой и нажатием список мог измениться, и удалилось бы не то устройство.
    """
    return hashlib.sha1(hwid.encode()).hexdigest()[:16]


def _device_title(raw: dict) -> str:
    model = raw.get("deviceModel") or raw.get("platform") or "Устройство"
    version = raw.get("osVersion")
    if raw.get("platform") and model != raw["platform"]:
        model = f"{model} ({raw['platform']}{f' {version}' if version else ''})"
    return model[:60]


@sync_to_async
def list_devices(user: NexUser) -> list[Device] | None:
    """None — панель не ответила; пустой список — устройств действительно нет."""
    subscription = Subscription.objects.filter(user=user).select_related("plan").first()
    if subscription is None:
        return []
    try:
        raw_devices = panel_sync.list_devices(subscription)
    except RemnawaveError as exc:
        logger.warning("Панель не отдала устройства для %s: %s", user.pk, exc)
        return None

    raw_devices.sort(key=lambda d: d.get("updatedAt") or d.get("createdAt") or "", reverse=True)
    return [
        Device(
            hwid=raw["hwid"],
            token=_device_token(raw["hwid"]),
            title=_device_title(raw),
            platform=raw.get("platform"),
            last_seen=(raw.get("updatedAt") or raw.get("createdAt") or "")[:10] or None,
        )
        for raw in raw_devices
    ]


@sync_to_async
def delete_device(user: NexUser, token: str) -> bool:
    """Удалить устройство по короткому токену. False — его уже нет."""
    subscription = Subscription.objects.filter(user=user).select_related("plan").first()
    if subscription is None:
        return False
    for raw in panel_sync.list_devices(subscription):
        if _device_token(raw["hwid"]) == token:
            panel_sync.remove_device(subscription, raw["hwid"])
            return True
    return False


@sync_to_async
def get_referral_view(user: NexUser) -> ReferralView:
    invitations = list(
        UserInvitation.objects.filter(inviter=user).select_related("invitee").order_by("-created_at")
    )
    rewarded = [item for item in invitations if item.reward_granted_at is not None]

    events = SubscriptionEvent.objects.filter(
        user=user, reason=SubscriptionEventReasonEnum.REFERRAL_INVITER
    ).order_by("-created_at")[:10]

    return ReferralView(
        link=f"{settings.TG_BOT_URL}?start={user.pk}",
        invited=len(invitations),
        pending=len(invitations) - len(rewarded),
        rewarded=len(rewarded),
        days_earned=sum(event.delta_days for event in events),
        history=[
            (event.created_at.strftime("%d.%m.%Y"), event.delta_days, event.comment)
            for event in events
        ],
    )


@sync_to_async
def create_connection_watch(user: NexUser, chat_id: int, message_id: int, known_hwids: set[str]) -> None:
    """Отметить, что человек прямо сейчас подключает устройство.

    Одна запись на пользователя: если он начал заново, старое ожидание
    неактуально — заменяем.
    """
    DeviceConnectionWatch.objects.update_or_create(
        user=user,
        defaults={
            "chat_id": chat_id,
            "message_id": message_id,
            "known_hwids": sorted(known_hwids),
        },
    )


@sync_to_async
def claim_connection_watch(user: NexUser) -> bool:
    """Забрать право сообщить об успехе. True — забрали мы, значит нам и слать.

    Тот же замок используется вебхуком: кто удалил строку первым, тот и
    отправляет. Иначе человек получил бы два сообщения об одном событии.
    """
    deleted, _ = DeviceConnectionWatch.objects.filter(user=user).delete()
    return bool(deleted)


@dataclass
class PeriodOption:
    months: int
    price: int
    saving: int
    discount_percent: int


@dataclass
class PlanOption:
    device_limit: int
    name: str
    price_month: int
    is_current: bool
    is_upgrade: bool
    converted_days: int
    topup_price: int | None


@sync_to_async
def get_renew_options(user: NexUser) -> tuple[Subscription | None, list[PeriodOption]]:
    subscription = Subscription.objects.select_related("plan").filter(user=user).first()
    if subscription is None:
        return None, []
    plan = subscription.plan
    options = [
        PeriodOption(
            months=period.months,
            price=period.price_for(plan),
            saving=period.saving_for(plan),
            discount_percent=period.discount_percent,
        )
        for period in BillingPeriod.objects.filter(is_active=True)
    ]
    return subscription, options


@sync_to_async
def get_plan_options(user: NexUser) -> tuple[Subscription | None, list[PlanOption]]:
    """Список тарифов с готовым расчётом перехода на каждый."""
    subscription = Subscription.objects.select_related("plan").filter(user=user).first()
    if subscription is None:
        return None, []
    subscription = service.ensure_current_plan(subscription)

    days_left = subscription.days_left
    current = subscription.plan
    options = []
    for plan in Plan.objects.filter(is_active=True, is_public=True):
        quote = pricing.quote_plan_change(days_left, current.price_month, plan.price_month)
        options.append(
            PlanOption(
                device_limit=plan.device_limit,
                name=plan.name,
                price_month=plan.price_month,
                is_current=plan.pk == current.pk,
                is_upgrade=plan.price_month > current.price_month,
                converted_days=quote.converted_days,
                topup_price=quote.topup_price,
            )
        )
    return subscription, options


@sync_to_async
def start_renew_payment(user: NexUser, months: int, return_url: str) -> str:
    """Создать платёж за продление и вернуть ссылку на оплату."""
    subscription = Subscription.objects.select_related("plan").get(user=user)
    period = BillingPeriod.objects.get(months=months, is_active=True)
    created = payments.create_payment(
        user,
        subscription.plan,
        amount=period.price_for(subscription.plan),
        days=period.days,
        months=period.months,
        return_url=return_url,
    )
    return created.url


@sync_to_async
def start_plan_change_payment(user: NexUser, device_limit: int, return_url: str) -> str:
    plan = Plan.objects.get(device_limit=device_limit, is_active=True)
    quote = service.quote_plan_change(user, plan)
    if quote.topup_price is None:
        raise service.SubscriptionError("Переход бесплатный, платить не нужно")
    created = payments.create_payment(
        user,
        plan,
        amount=quote.topup_price,
        days=quote.topup_days,
        kind=PaymentKindEnum.PLAN_CHANGE,
        return_url=return_url,
    )
    return created.url


@sync_to_async
def change_plan_free(user: NexUser, device_limit: int) -> Subscription:
    """Бесплатный переход: повышение с пересчётом остатка или отложенное понижение."""
    plan = Plan.objects.get(device_limit=device_limit, is_active=True)
    subscription = Subscription.objects.select_related("plan").get(user=user)
    if plan.price_month < subscription.plan.price_month:
        result = service.schedule_plan_downgrade(user, plan)
    else:
        result = service.change_plan_now(user, plan)
    _sync_quietly(result)
    return result


@sync_to_async
def set_email(user: NexUser, email: str) -> None:
    user.email = email
    user.save(update_fields=["email"])


@sync_to_async
def receipt_needs_email(user: NexUser) -> bool:
    """Email спрашиваем, только пока выписываются чеки: иначе он ни к чему."""
    return GlobalSettings.load().receipt_enabled and not user.email


@dataclass
class MaintenanceState:
    enabled: bool
    message: str
    until: object
    affects_admin: bool


@sync_to_async
def get_maintenance() -> MaintenanceState:
    settings_obj = GlobalSettings.load()
    return MaintenanceState(
        enabled=settings_obj.maintenance_mode,
        message=settings_obj.maintenance_message,
        until=settings_obj.maintenance_until,
        affects_admin=settings_obj.maintenance_affects_admin,
    )


def _sync_quietly(subscription: Subscription) -> None:
    """Панель может лежать — подписка останется PENDING, её добьёт celery."""
    try:
        panel_sync.sync_subscription(subscription)
    except RemnawaveError as exc:
        logger.warning("Не удалось сразу синхронизировать подписку %s: %s", subscription.pk, exc)
