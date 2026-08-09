from django.db import models


class SubscriptionEventReasonEnum(models.TextChoices):
    TRIAL = "trial", "Пробный период нового пользователя"
    LEGACY_MIGRATION = "legacy_migration", "Перенос баланса из старой версии"
    LEGACY_NO_DEVICES = "legacy_no_devices", "Компенсация за отсутствие устройств"
    PURCHASE = "purchase", "Оплата подписки"
    PLAN_UPGRADE = "plan_upgrade", "Переход на больший тариф"
    PLAN_DOWNGRADE_SCHEDULED = "plan_downgrade_scheduled", "Запланирован переход на меньший тариф"
    PLAN_DOWNGRADE_APPLIED = "plan_downgrade_applied", "Применён переход на меньший тариф"
    REFERRAL_INVITER = "referral_inviter", "Бонус за приглашённого пользователя"
    REFERRAL_INVITEE = "referral_invitee", "Бонус за приход по реферальной ссылке"
    PROMO_CODE = "promo_code", "Применение промокода"
    ADMIN_ADJUSTMENT = "admin_adjustment", "Изменено администратором"
    EXPIRED = "expired", "Подписка истекла"
