"""Общая защита для всех тестов: наружу — ни одного запроса.

Появился после того, как прогон тестов дважды сходил в **боевую** панель
Remnawave и завёл там пользователей. Причина не в невнимательности: локальный
`.env` смотрит на боевую панель, а сервисный код честно синхронизирует
подписку при любом изменении тарифа — в бою это правильно, в тестах
катастрофично.

Полагаться на то, что каждый новый тест не забудет замокать панель, нельзя:
забудут, и узнаем об этом снова по лишним пользователям в проде. Поэтому сеть
закрыта по умолчанию, а тому, кто действительно хочет ходить наружу, надо
попросить об этом явно — маркером `@pytest.mark.allow_network`.
"""

import pytest


class NetworkAccessInTests(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_outbound_requests(request, monkeypatch):
    if request.node.get_closest_marker("allow_network"):
        return

    def blocked(*args, **kwargs):
        raise NetworkAccessInTests(
            "Тест попытался сходить в сеть. Локальный .env смотрит на боевую "
            "панель — замокай клиент или помечай тест @pytest.mark.allow_network."
        )

    import httpx

    for name in ("request", "get", "post", "patch", "put", "delete", "stream"):
        monkeypatch.setattr(httpx, name, blocked, raising=False)
    monkeypatch.setattr(httpx.Client, "request", blocked, raising=False)
    monkeypatch.setattr(httpx.Client, "send", blocked, raising=False)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: тесту действительно нужен настоящий сетевой запрос"
    )
