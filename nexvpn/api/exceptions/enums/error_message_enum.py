from enum import Enum


class ErrorMessageEnum(Enum):
    NO_EMAIL_ERROR_MESSAGE = "Укажите email — на него придёт чек об оплате."
    DEVICE_LIMIT_REACHED_ERROR_MESSAGE = (
        "Достигнут лимит устройств по вашему тарифу.\n"
        "Удалите одно из устройств или перейдите на тариф побольше."
    )
    NO_SUBSCRIPTION_ERROR_MESSAGE = "У вас пока нет активной подписки."
    PANEL_UNAVAILABLE_ERROR_MESSAGE = "Сервис временно недоступен, попробуйте через пару минут."
