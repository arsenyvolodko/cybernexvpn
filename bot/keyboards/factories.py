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


class BroadcastCallback(CallbackData, prefix="bc"):
    """Подтверждение рассылки, составленной в боте.

    Идентификатор черновика — прямо в кнопке, а не в FSM: любое нажатие кнопки
    сбрасывает состояние (см. `StateResetMiddleware`), да и подтверждать
    отправку трёмстам людям по «висящему» состоянию опасно — кнопка должна
    работать ровно с тем черновиком, под которым её показали.
    """

    broadcast_id: int
    action: str  # send | drop
