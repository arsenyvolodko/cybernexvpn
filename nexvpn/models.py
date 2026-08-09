import math
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.timezone import now

from nexvpn.enums import (
    PanelSyncStatusEnum,
    PaymentKindEnum,
    PaymentModeEnum,
    PaymentSubjectEnum,
    SubscriptionEventReasonEnum,
    TransactionStatusEnum,
    TransactionTypeEnum,
    VatCodeEnum,
)

User = get_user_model()


class NexUser(models.Model):
    """Пользователь сервиса. `id` == telegram id, задаётся явно при создании."""

    username = models.CharField(max_length=63, null=True)
    first_name = models.CharField(max_length=64, null=True, blank=True)
    email = models.EmailField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.OneToOneField(User, null=True, default=None, on_delete=models.SET_NULL, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True)

    # Пришёл из старой (WireGuard) версии сервиса — таким пробный период не выдаём.
    is_legacy = models.BooleanField(default=False)
    # Первый заход в бот новой версии. None = ещё не заходил.
    activated_at = models.DateTimeField(null=True, blank=True, default=None)

    @property
    def is_activated(self) -> bool:
        return self.activated_at is not None

    @property
    def panel_username(self) -> str:
        """Логин этого пользователя в Remnawave."""
        return f"tg_{self.pk}"

    def __str__(self):
        return self.username or str(self.pk)


class UserInvitation(models.Model):
    """Кто кого привёл. Бонус инвайтеру начисляется после первой оплаты приглашённого."""

    inviter = models.ForeignKey(NexUser, on_delete=models.CASCADE, related_name="sent_invitations")
    invitee = models.OneToOneField(NexUser, on_delete=models.CASCADE, related_name="invitation")
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    inviter_notified_at = models.DateTimeField(null=True, blank=True, default=None)
    reward_granted_at = models.DateTimeField(null=True, blank=True, default=None)

    def __str__(self):
        return f"{self.inviter} → {self.invitee}"


class Plan(models.Model):
    """Тариф: сколько устройств и почём в месяц. Цены правятся в админке."""

    device_limit = models.PositiveSmallIntegerField(unique=True)
    price_month = models.PositiveIntegerField(help_text="Цена за 30 дней, ₽")
    name = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True, help_text="Можно купить или перейти")
    is_public = models.BooleanField(default=True, help_text="Показывать в списке тарифов")
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "device_limit"]

    @property
    def price_day(self) -> float:
        """Только для отображения. Все расчёты идут в целых числах, см. subscription.pricing."""
        return self.price_month / settings.DAYS_IN_PERIOD

    def __str__(self):
        return f"{self.name} — {self.device_limit} устр., {self.price_month}₽/мес"


class Subscription(models.Model):
    """Одна подписка на пользователя: тариф + дата окончания. Устройства живут в Remnawave."""

    user = models.OneToOneField(NexUser, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    # Заказанный переход на меньший тариф: применяется при следующем продлении.
    next_plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, null=True, blank=True, default=None,
        related_name="scheduled_subscriptions",
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Состояние в Remnawave. Идентификатор пользователя там — целое число,
    # плоского uuid у него нет; shortUuid — только для URL подписки.
    panel_user_id = models.PositiveIntegerField(null=True, blank=True, default=None)
    panel_short_uuid = models.CharField(max_length=63, null=True, blank=True, default=None)
    subscription_url = models.URLField(max_length=255, null=True, blank=True, default=None)
    panel_status = models.CharField(
        max_length=31, choices=PanelSyncStatusEnum.choices, default=PanelSyncStatusEnum.NEVER_SYNCED,
    )
    panel_synced_at = models.DateTimeField(null=True, blank=True, default=None)
    panel_error = models.TextField(blank=True, default="")

    # Задел под автоплатёж YooKassa: пока не используется.
    payment_method_id = models.CharField(max_length=63, null=True, blank=True, default=None)
    auto_renew_agreed = models.BooleanField(default=False)

    @property
    def is_active(self) -> bool:
        return self.expires_at > now()

    @property
    def days_left(self) -> int:
        """Остаток в днях, неполный день считается полным. 0 если подписка истекла."""
        delta = self.expires_at - now()
        if delta.total_seconds() <= 0:
            return 0
        return math.ceil(delta.total_seconds() / 86400)

    @property
    def device_limit(self) -> int:
        return self.plan.device_limit

    @property
    def needs_panel_sync(self) -> bool:
        return self.panel_status != PanelSyncStatusEnum.SYNCED

    def mark_panel_dirty(self, save: bool = True) -> None:
        self.panel_status = PanelSyncStatusEnum.PENDING
        if save:
            self.save(update_fields=["panel_status", "updated_at"])

    def __str__(self):
        return f"{self.user}: {self.plan.device_limit} устр. до {self.expires_at:%d.%m.%Y}"


class SubscriptionEvent(models.Model):
    """Журнал дней. Любое изменение expires_at или тарифа оставляет здесь запись."""

    user = models.ForeignKey(NexUser, on_delete=models.CASCADE, related_name="subscription_events")
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="events",
    )
    reason = models.CharField(max_length=31, choices=SubscriptionEventReasonEnum.choices)
    delta_days = models.IntegerField(default=0)

    # Снимок условий на момент события: цены в Plan меняются, история — нет.
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, null=True, blank=True, default=None)
    price_month = models.PositiveIntegerField(null=True, blank=True, default=None)
    amount = models.PositiveIntegerField(default=0, help_text="Сколько заплачено, ₽")

    payment = models.ForeignKey("Payment", on_delete=models.SET_NULL, null=True, blank=True, default=None)
    expires_at_before = models.DateTimeField(null=True, blank=True, default=None)
    expires_at_after = models.DateTimeField(null=True, blank=True, default=None)
    comment = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.delta_days >= 0 else ""
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.user}: {sign}{self.delta_days} дн. ({self.get_reason_display()})"


class LegacyMigrationRecord(models.Model):
    """Снимок того, что было у пользователя в старой версии, и как это сконвертировали.

    Пишется дата-миграцией до удаления WireGuard-таблиц: после их дропа это
    единственный источник правды по тому, откуда взялись стартовые дни.
    """

    user = models.OneToOneField(NexUser, on_delete=models.CASCADE, related_name="legacy_record")
    balance = models.IntegerField(help_text="Баланс на момент миграции, ₽")
    device_count = models.PositiveIntegerField(help_text="Активных устройств на дату среза")
    devices_trimmed = models.PositiveIntegerField(default=0, help_text="Сколько устройств срезано сверх лимита 10")
    remaining_paid_days = models.PositiveIntegerField(default=0, help_text="Остаток оплаченного периода, дн.")
    days_from_balance = models.PositiveIntegerField(default=0)
    days_granted = models.PositiveIntegerField(default=0, help_text="Итого начислено, после потолка и порога")
    capped = models.BooleanField(default=False, help_text="Срезано потолком LEGACY_MAX_DAYS")
    floored = models.BooleanField(default=False, help_text="Поднято нижним порогом LEGACY_MIN_DAYS")
    unlimited = models.BooleanField(default=False, help_text="Баланс был залит вручную → безлимит")
    plan_device_limit = models.PositiveSmallIntegerField()
    cutoff_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}: {self.balance}₽ / {self.device_count} устр. → {self.days_granted} дн."


class GlobalSettings(models.Model):
    """Глобальные настройки сервиса. Одна строка, правится в админке.

    Живут в БД, а не в settings/.env, чтобы менять их без деплоя: включить чеки
    в тот день, когда подключится касса, или вывесить заглушку на бота посреди
    ночи. В описании платежа и в позиции чека не должно быть слова VPN: строка
    видна и в чеке, и в выписке по карте.
    """

    # --- технические работы ---
    maintenance_mode = models.BooleanField(
        default=False,
        verbose_name="Технические работы",
        help_text="Бот отвечает заглушкой вместо обычных сценариев. "
                  "На приём платежей и синхронизацию с панелью не влияет.",
    )
    maintenance_message = models.TextField(
        default="Ведём технические работы, сервис скоро вернётся. Подписка идёт, "
                "ничего не потеряется.",
        verbose_name="Текст заглушки",
    )
    maintenance_until = models.DateTimeField(
        null=True, blank=True, default=None,
        verbose_name="Ориентировочно до",
        help_text="Необязательно. Если указать, бот сможет назвать срок в заглушке.",
    )

    # --- чек 54-ФЗ ---
    receipt_enabled = models.BooleanField(
        default=False,
        verbose_name="Выписывать чек",
        help_text="Включать только когда касса подключена в личном кабинете YooKassa. "
                  "Пока выключено, email при оплате не спрашивается.",
    )
    vat_code = models.PositiveSmallIntegerField(
        choices=VatCodeEnum.choices, default=VatCodeEnum.NO_VAT, verbose_name="Ставка НДС",
    )
    payment_subject = models.CharField(
        max_length=31, choices=PaymentSubjectEnum.choices, default=PaymentSubjectEnum.SERVICE,
        verbose_name="Признак предмета расчёта",
    )
    payment_mode = models.CharField(
        max_length=31, choices=PaymentModeEnum.choices, default=PaymentModeEnum.FULL_PAYMENT,
        verbose_name="Признак способа расчёта",
    )
    payment_description = models.CharField(
        max_length=128, default="Оплата подписки CyberNex",
        verbose_name="Описание платежа",
        help_text="Видно в выписке по карте. Доступны подстановки {devices} и {days}.",
    )
    purchase_item_template = models.CharField(
        max_length=128, default="Подписка CyberNex: {devices} устр., {days} дн.",
        verbose_name="Позиция чека — оплата подписки",
        help_text="Подстановки: {devices} — устройств по тарифу, {days} — дней.",
    )
    plan_change_item_template = models.CharField(
        max_length=128, default="Изменение тарифа подписки CyberNex: {devices} устр., {days} дн.",
        verbose_name="Позиция чека — смена тарифа",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Глобальные настройки"
        verbose_name_plural = "Глобальные настройки"

    @classmethod
    def load(cls) -> "GlobalSettings":
        return cls.objects.get_or_create(pk=1)[0]

    def save(self, *args, **kwargs):
        # Синглтон: вторую строку завести нельзя, иначе непонятно, какая в силе.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Глобальные настройки удалять нельзя")

    def __str__(self):
        return "Глобальные настройки"


class Payment(models.Model):
    """Платёж в YooKassa. uuid — их идентификатор, ключ идемпотентности наш.

    Здесь же хранится намерение: за что заплачено. Вебхук не должен ничего
    доверять телу уведомления — он смотрит в эту запись и выдаёт ровно то,
    что было посчитано на нашей стороне при создании платежа.
    """

    uuid = models.UUIDField(primary_key=True)
    idempotence_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    user = models.ForeignKey(NexUser, on_delete=models.CASCADE, null=True, blank=True, default=None)
    kind = models.CharField(max_length=31, choices=PaymentKindEnum.choices, null=True, blank=True, default=None)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, null=True, blank=True, default=None)
    amount = models.PositiveIntegerField(null=True, blank=True, default=None)
    # Ставится ровно один раз: YooKassa шлёт вебхук повторно, пока не получит 200.
    processed_at = models.DateTimeField(null=True, blank=True, default=None)

    def __str__(self):
        return f"{self.uuid} — {self.amount}₽"


class PromoCode(models.Model):
    name = models.CharField(max_length=31, unique=True)
    bonus_days = models.PositiveIntegerField(default=0, help_text="Сколько дней подписки даёт")
    value = models.IntegerField(null=True, blank=True, default=None, help_text="Легаси: номинал в ₽")
    is_active = models.BooleanField(default=True)
    public_access = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name}: {self.bonus_days} дн."


class UsedPromoCode(models.Model):
    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    promo_code = models.ForeignKey(PromoCode, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.user} - {self.promo_code}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "promo_code"], name="unique_used_user_promo_code")
        ]


class AllowedUserPromoCode(models.Model):
    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    promo_code = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name="allowed_users")

    def __str__(self):
        return f"{self.user} - {self.promo_code}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "promo_code"], name="unique_allowed_user_promo_code")
        ]


class Transaction(models.Model):
    """Журнал денег. Дни начисляет SubscriptionEvent, здесь только рубли."""

    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    is_credit = models.BooleanField()
    value = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, null=True, default=None)
    promo_code = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, null=True, default=None)
    type = models.CharField(max_length=31, choices=TransactionTypeEnum.choices)
    status = models.CharField(
        max_length=31,
        choices=TransactionStatusEnum.choices,
        default=TransactionStatusEnum.SUCCEEDED,
    )

    def __str__(self):
        timestamp = self.created_at.strftime(format="%d.%m.%Y %H:%M:%S")
        credit_type = "Пополнение" if self.is_credit else "Списание"
        status, type_ = self.get_status_display(), self.get_type_display()
        return f"{timestamp}: {credit_type} в размере {self.value}₽ - [{status}] - {type_}."
