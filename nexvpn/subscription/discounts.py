"""Скидки и промокоды: какие тарифы видит человек и почём.

Всё, что показывает или берёт деньги, спрашивает цены отсюда — через
`PriceBook`. Экран, сумма платежа и проверка доплаты в вебхуке считаются по
одной книге, иначе однажды человек увидит одну цену, а заплатит другую — или
заплатит, а вебхук сочтёт это недоплатой и не сменит тариф.
"""

import logging
import re
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils.timezone import now

from nexvpn.models import Discount, NexUser, Plan, UserDiscount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBook:
    """Цены для одного человека. Без скидки — каталог."""

    discount: Discount | None = None
    prices: dict[int, int] = field(default_factory=dict)  # plan_id → ₽ за 30 дней
    via: str = ""  # как человек получил скидку: code / request

    def price_month(self, plan: Plan) -> int:
        return self.prices.get(plan.pk, plan.price_month)

    def is_discounted(self, plan: Plan) -> bool:
        return plan.pk in self.prices

    def mark(self, plan: Plan) -> str:
        """Надпись для кнопки с ценой — только если цена этого тарифа по скидке."""
        return self.discount.button_mark if self.discount is not None and self.is_discounted(plan) else ""

    def visible_plans(self) -> list[Plan]:
        """Тарифы, которые можно выбрать. Со скидкой — только её тарифы."""
        plans = Plan.objects.filter(is_active=True)
        if self.discount is not None:
            return list(plans.filter(pk__in=self.prices))
        return list(plans.filter(is_public=True))

    def can_choose(self, plan: Plan) -> bool:
        return any(p.pk == plan.pk for p in self.visible_plans())


def book_of(discount: Discount | None, via: str = "") -> PriceBook:
    """Книга по конкретной скидке — например, записанной в платёж."""
    if discount is None:
        return PriceBook()
    prices = dict(discount.prices.values_list("plan_id", "price_month"))
    return PriceBook(discount=discount, prices=prices, via=via)


def active_discount(user: NexUser) -> UserDiscount | None:
    """Действующая скидка человека. Истёкшая или выключенная — как нет."""
    return (
        UserDiscount.objects.filter(
            user=user,
            status=UserDiscount.Status.ACTIVE,
            discount__is_active=True,
            discount__valid_until__gt=now(),
        )
        .select_related("discount")
        .order_by("-created_at")
        .first()
    )


def book_for(user: NexUser) -> PriceBook:
    held = active_discount(user)
    return book_of(held.discount, held.via) if held else PriceBook()


# --- применение ---


def normalize_code(raw: str) -> str:
    return (raw or "").strip()


@dataclass(frozen=True)
class CodeResult:
    """Что вышло с промокодом. `discount` есть только у применённого."""

    discount: Discount | None
    # applied — применён; not_found — такого кода нет; unavailable — код есть,
    # но истёк, выключен или выдан не этому человеку.
    status: str


def find_code(user: NexUser, raw: str) -> CodeResult:
    """Промокод, который этот человек может применить прямо сейчас."""
    code = normalize_code(raw)
    if not re.match(Discount.CODE_PATTERN, code):
        return CodeResult(None, "not_found")
    # Код бывает и у скидки с подтверждением — тогда код даёт её сразу, без заявки.
    discount = Discount.objects.filter(code__iexact=code).exclude(code="").first()
    if discount is None:
        return CodeResult(None, "not_found")
    if not discount.is_valid() or not discount.available_to(user.pk):
        return CodeResult(None, "unavailable")
    return CodeResult(discount, "applied")


@transaction.atomic
def activate(user: NexUser, discount: Discount, via: str = UserDiscount.Via.CODE) -> UserDiscount:
    """Сделать скидку действующей. Прежняя действующая становится «заменена»."""
    UserDiscount.objects.filter(user=user, status=UserDiscount.Status.ACTIVE).update(
        status=UserDiscount.Status.REPLACED, decided_at=now()
    )
    return UserDiscount.objects.create(
        user=user, discount=discount, status=UserDiscount.Status.ACTIVE, decided_at=now(), via=via
    )


def apply_code(user: NexUser, raw: str) -> CodeResult:
    """Ввод промокода: применяет, если можно, и говорит, что вышло."""
    result = find_code(user, raw)
    if result.discount is not None:
        activate(user, result.discount)
    return result


def menu_discounts(user: NexUser) -> list[Discount]:
    """Скидки с подтверждением, у которых есть кнопка в разделе меню."""
    return [
        d
        for d in Discount.objects.filter(
            kind=Discount.Kind.VERIFICATION, show_in_menu=True, is_active=True, valid_until__gt=now()
        ).order_by("created_at")
        if d.available_to(user.pk)
    ]


# --- заявки на подтверждение ---


def request_state(user: NexUser, discount: Discount) -> str:
    """Где человек со скидкой: active / pending / none."""
    held = active_discount(user)
    if held is not None and held.discount_id == discount.pk:
        return "active"
    if UserDiscount.objects.filter(user=user, discount=discount, status=UserDiscount.Status.PENDING).exists():
        return "pending"
    return "none"


def open_request(user: NexUser, discount: Discount, replace_before=None) -> tuple[UserDiscount, bool]:
    """Заявка на проверке. Второе значение — создана ли она сейчас.

    Альбом из нескольких фото приходит пачкой параллельных апдейтов; создать
    заявку должен ровно один из них — его и видно по `True`.

    `replace_before` — человек прислал подтверждение заново, пока старая
    заявка висела. Старую (созданную раньше этого момента) заменяем новой.
    Граница по времени, а не «все на проверке»: иначе вторая часть альбома
    заменила бы заявку, которую только что открыла первая.
    """
    if replace_before is not None:
        UserDiscount.objects.filter(
            user=user, discount=discount, status=UserDiscount.Status.PENDING, created_at__lt=replace_before
        ).update(status=UserDiscount.Status.REPLACED, decided_at=now())
    existing = UserDiscount.objects.filter(
        user=user, discount=discount, status=UserDiscount.Status.PENDING
    ).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            return (
                UserDiscount.objects.create(
                    user=user, discount=discount, status=UserDiscount.Status.PENDING, via=UserDiscount.Via.REQUEST
                ),
                True,
            )
    except IntegrityError:
        return UserDiscount.objects.get(user=user, discount=discount, status=UserDiscount.Status.PENDING), False


@transaction.atomic
def decide(request_id: int, approve: bool) -> UserDiscount | None:
    """Решение админа. None — заявку уже разобрали (или её нет)."""
    request = (
        UserDiscount.objects.select_for_update()
        .select_related("discount", "user")
        .filter(pk=request_id, status=UserDiscount.Status.PENDING)
        .first()
    )
    if request is None:
        return None
    if approve:
        UserDiscount.objects.filter(user=request.user, status=UserDiscount.Status.ACTIVE).update(
            status=UserDiscount.Status.REPLACED, decided_at=now()
        )
        request.status = UserDiscount.Status.ACTIVE
    else:
        request.status = UserDiscount.Status.REJECTED
    request.decided_at = now()
    request.save(update_fields=["status", "decided_at"])
    return request


def parse_start_payload(payload: str) -> tuple[str, str]:
    """`/start` → (id пригласившего, код промокода). Любая часть может быть пустой.

    Форматы: `506954303`, `promo_КОД`, `506954303_promo_КОД`. Амперсанда в
    ссылке быть не может: Telegram пропускает в start только буквы, цифры, `_`
    и `-`, до 64 символов.
    """
    payload = (payload or "").strip()
    referral, _, code = payload.partition("promo_")
    referral = referral.rstrip("_")
    return referral, code
