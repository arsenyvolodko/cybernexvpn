from django.db import models


class TransactionTypeEnum(models.TextChoices):
    RENEW_SUBSCRIPTION = "renew_subscription", "Продление подписки"
    REACTIVATE_CLIENT = "reactivate_clint", "Возобновление подписки"
    INVITATION = "invitation", "Приглашение пользователя"
    START_BALANCE = "start_balance", "Стартовый баланс"
    FILL_UP_BALANCE = "fill_up_balance", "Пополнение баланса"
    ADD_DEVICE = "add_device", "Добавление устройства"
    PROMO_CODE = "promo_code", "Применение промокода"
    UPDATED_BY_ADMIN = "updated_by_admin", "Изменено администратором"
