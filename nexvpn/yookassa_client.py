"""Сетевые настройки SDK ЮKassa: таймаут запроса и политика повторов.

SDK отправляет запрос без ограничения по времени. `Configuration.timeout`,
вопреки названию, — это пауза между повторами, а не таймаут обращения. Пока
ЮKassa отвечает, разницы не видно; когда перестаёт — а нас регулярно режут по
IP хостера, — вызов висит десятками минут.

Висит он не в фоне: `Payment.create` уходит из обработчика бота через
`sync_to_async` и занимает поток из пула. Нескольких нажатий хватает, чтобы пул
кончился и бот перестал отвечать вообще на всё, а не только на кнопку оплаты.
В логах это выглядело как `Duration 2394981 ms` — сорок минут на один апдейт.

Поэтому здесь две правки:

* жёсткий таймаут на запрос — тот же `YOOKASSA_TIMEOUT`, что и у сверки;
* повторы оставлены только для HTTP 202 («платёж ещё обрабатывается»), но
  убраны для обрывов связи. Сеть, не ответившая за таймаут, за второй такой же
  не ответит, а человек всё это время смотрит на застывшую кнопку — пусть
  лучше сразу получит «оплата недоступна» и решит сам, нажимать ли ещё раз.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry
from yookassa.client import ApiClient


class _SessionWithTimeout(requests.Session):
    """Сессия с таймаутом по умолчанию.

    Отдельный класс, а не аргумент вызова: точка обращения спрятана внутри SDK,
    дотянуться до неё можно только через сессию.
    """

    def __init__(self, timeout: int):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


def install(timeout: int) -> None:
    """Подменить фабрику сессий в SDK. Вызывается один раз при старте приложения."""

    def get_session(self):
        retries = Retry(
            # Считаем повторы раздельно, поэтому общий счётчик не задаём.
            total=None,
            connect=0,
            read=0,
            status=self.max_attempts,
            backoff_factor=self.timeout / 1000,
            allowed_methods=["POST"],
            status_forcelist=[202],
        )
        session = _SessionWithTimeout(timeout)
        session.mount("https://", HTTPAdapter(max_retries=retries))
        return session

    ApiClient.get_session = get_session
