from aiogram.filters.callback_data import CallbackData


class DeviceCallback(CallbackData, prefix="dev"):
    """Устройство адресуется коротким хешем HWID, а не индексом в списке.

    Индекс сломался бы, если между отрисовкой и нажатием список изменился, —
    и удалилось бы чужое устройство.
    """

    token: str
    action: str  # open | delete | confirm


class ConnectCallback(CallbackData, prefix="c"):
    """Шаг сценария подключения. Состояние — прямо здесь, FSM не нужен:
    старое сообщение остаётся рабочим даже через час."""

    platform: str
    step: str  # download | connect


class RenewCallback(CallbackData, prefix="renew"):
    months: int


class PlanCallback(CallbackData, prefix="plan"):
    device_limit: int
    action: str  # open | free | pay


class FaqCallback(CallbackData, prefix="faq"):
    topic: str
