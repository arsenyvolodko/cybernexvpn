"""Приём проб адресов выдачи подписки.

Точка публичная и без токена — его негде спрятать, страницу-мостик видно
целиком. Поэтому здесь проверяется в первую очередь, что чужое в базу не
попадает: неизвестный адрес, раздутое тело, мусор вместо чисел.
"""

import json

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from nexvpn.api.tests.factories.subscription_factories import SubscriptionFactory
from nexvpn.models import SubHostProbe

pytestmark = pytest.mark.django_db

URL = "/api/v1/telemetry/sub-probe/"
HOSTS = ["sub-nex.com", "sub.cybernexapp.com"]


@pytest.fixture
def client(settings):
    settings.SUB_PROBE_HOSTS = HOSTS
    # Потолок частоты живёт в кэше процесса, а он общий на весь прогон.
    cache.clear()
    return APIClient()


def beacon(client, payload):
    """Так шлёт браузер: sendBeacon ставит text/plain, не application/json."""
    return client.post(URL, json.dumps(payload), content_type="text/plain")


def test_results_are_stored(client):
    res = beacon(client, {
        "platform": "ios",
        "results": [
            {"host": "sub-nex.com", "ok": True, "ms": 412},
            {"host": "sub.cybernexapp.com", "ok": False, "ms": 0},
        ],
    })

    assert res.status_code == 200
    assert res.data["stored"] == 2
    stored = {p.host: p for p in SubHostProbe.objects.all()}
    assert stored["sub-nex.com"].outcome == SubHostProbe.Outcome.OK
    assert stored["sub-nex.com"].ms == 412
    assert stored["sub.cybernexapp.com"].outcome == SubHostProbe.Outcome.FAILED
    assert stored["sub-nex.com"].platform == "ios"


def test_json_content_type_also_works(client):
    """Мостик шлёт маячком, но ручная проверка ходит обычным json."""
    res = client.post(URL, {"results": [{"host": "sub-nex.com", "ok": True, "ms": 10}]}, format="json")

    assert res.status_code == 200
    assert SubHostProbe.objects.count() == 1


def test_slow_answer_is_not_the_same_as_a_good_one(client):
    """Блокировка чаще душит, чем рвёт, — медленный ответ надо отличать."""
    beacon(client, {"results": [{"host": "sub-nex.com", "ok": True, "ms": 9000}]})

    assert SubHostProbe.objects.get().outcome == SubHostProbe.Outcome.SLOW


def test_unknown_host_is_dropped(client):
    """Иначе кто угодно нальёт нам статистику по своим доменам."""
    res = beacon(client, {"results": [
        {"host": "evil.example", "ok": False, "ms": 1},
        {"host": "sub-nex.com", "ok": True, "ms": 1},
    ]})

    assert res.data["stored"] == 1
    assert SubHostProbe.objects.get().host == "sub-nex.com"


def test_number_of_rows_is_capped(client):
    rows = [{"host": "sub-nex.com", "ok": True, "ms": 1} for _ in range(50)]

    beacon(client, {"results": rows})

    assert SubHostProbe.objects.count() == 8


def test_garbage_does_not_blow_up(client):
    assert beacon(client, {"results": "не список"}).status_code == 400
    assert client.post(URL, "}{", content_type="text/plain").status_code == 400
    # Строка вместо числа и посторонний тип строки — пропускаем, не падаем.
    res = beacon(client, {"results": ["строка", {"host": "sub-nex.com", "ok": True, "ms": "быстро"}]})
    assert res.data["stored"] == 1
    assert SubHostProbe.objects.get().ms == 0


def test_probe_is_linked_to_the_person(client):
    sub = SubscriptionFactory(panel_short_uuid="ShFz9bu699hZmcYH")

    beacon(client, {
        "short_uuid": "ShFz9bu699hZmcYH",
        "results": [{"host": "sub-nex.com", "ok": True, "ms": 100}],
    })

    assert SubHostProbe.objects.get().user_id == sub.user_id


def test_unknown_short_uuid_still_leaves_the_probe(client):
    """Мостик открывают и по прямой ссылке — такую пробу терять жалко."""
    beacon(client, {
        "short_uuid": "нетакого",
        "results": [{"host": "sub-nex.com", "ok": True, "ms": 100}],
    })

    probe = SubHostProbe.objects.get()
    assert probe.user_id is None
    assert probe.short_uuid == "нетакого"


def test_limit_is_counted_per_person_not_per_relay(client):
    """Отчёты идут через РФ-зеркало, и адрес соединения у всех один и тот же.

    Если потолок частоты считать по нему, первые же несколько человек съедят
    его на всех и остальные пробы молча пропадут. Спасает цепочка
    `X-Forwarded-For`: nginx дописывает в неё настоящий адрес. Ошибка тут
    была бы невидимой — данные просто перестали бы доходить.
    """
    body = {"results": [{"host": "sub-nex.com", "ok": True, "ms": 1}]}
    relay = "203.0.113.7"  # адрес зеркала, одинаковый для всех

    for i in range(70):
        res = client.post(
            URL, json.dumps(body), content_type="text/plain",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}, {relay}",
        )
        assert res.status_code == 200, f"человека №{i} срезал потолок частоты"

    assert SubHostProbe.objects.count() == 70
