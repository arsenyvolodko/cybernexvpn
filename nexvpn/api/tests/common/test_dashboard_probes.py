"""Блок «Выдача ссылки»: что показывают замеры с устройств людей.

Главное здесь — расхождение, а не доля отказов. Если оба адреса отказывают у
одних и тех же людей, выбирать не из чего, и никакая суммарная доля этого не
покажет. Поэтому расхождение считается по последнему исходу на человека, а
людей, у которых проверен только один адрес, в расчёт не берём — иначе
«работает только один» смешается с «второй не проверяли».
"""

import datetime as dt

import pytest
from django.utils.timezone import now

from nexvpn.api.tests.factories import NexUserFactory
from nexvpn.dashboard.probes import host_health
from nexvpn.models import SubHostProbe

pytestmark = pytest.mark.django_db

RU = "sub-nex.com"
EU = "sub.cybernexapp.com"
TODAY = dt.date.today()


def probe(host, outcome, ms=100, user=None, uuid="", when=None):
    row = SubHostProbe.objects.create(
        host=host, outcome=outcome, ms=ms, user=user, short_uuid=uuid,
    )
    if when:
        SubHostProbe.objects.filter(pk=row.pk).update(created_at=when)
        row.refresh_from_db()
    return row


def health():
    return host_health(TODAY - dt.timedelta(days=7), TODAY)


def test_empty_period_does_not_break():
    data = health()

    assert data["probes"] == 0
    assert data["hosts"] == []
    assert data["split"]["checked"] == 0


def test_counts_and_share_per_host():
    probe(RU, SubHostProbe.Outcome.OK, ms=200)
    probe(RU, SubHostProbe.Outcome.SLOW, ms=4000)
    probe(RU, SubHostProbe.Outcome.FAILED, ms=5000)

    row = next(h for h in health()["hosts"] if h["host"] == RU)

    assert (row["ok"], row["slow"], row["failed"], row["total"]) == (1, 1, 1, 3)
    # «Удалось» — и быстрые, и медленные: ссылка доехала.
    assert row["share_ok"] == 67


def test_median_ignores_failures():
    """У отказа время — это таймаут, а не скорость канала: он бы всё задрал."""
    probe(RU, SubHostProbe.Outcome.OK, ms=100)
    probe(RU, SubHostProbe.Outcome.OK, ms=300)
    probe(RU, SubHostProbe.Outcome.FAILED, ms=5000)

    row = next(h for h in health()["hosts"] if h["host"] == RU)

    assert row["median_ms"] == 200


def test_split_counts_people_where_only_one_host_works():
    good = NexUserFactory()
    probe(RU, SubHostProbe.Outcome.OK, user=good)
    probe(EU, SubHostProbe.Outcome.FAILED, user=good)

    split = health()["split"]

    assert split["checked"] == 1
    assert split["only_one"] == 1
    assert split["both_ok"] == 0
    assert split["only_by_host"] == [{"host": RU, "title": "sub-nex.com (через РФ-зеркало)", "users": 1}]


def test_person_with_one_host_checked_is_not_counted():
    lonely = NexUserFactory()
    probe(RU, SubHostProbe.Outcome.OK, user=lonely)

    assert health()["split"]["checked"] == 0


def test_latest_outcome_wins_for_a_person():
    """Важно не «сколько раз отказало», а работает ли адрес у него сейчас."""
    person = NexUserFactory()
    yesterday = now() - dt.timedelta(days=1)
    probe(RU, SubHostProbe.Outcome.FAILED, user=person, when=yesterday)
    probe(EU, SubHostProbe.Outcome.OK, user=person, when=yesterday)
    probe(RU, SubHostProbe.Outcome.OK, user=person)
    probe(EU, SubHostProbe.Outcome.OK, user=person)

    split = health()["split"]

    assert split["both_ok"] == 1
    assert split["only_one"] == 0


def test_probes_without_a_person_still_count_by_short_uuid():
    """Мостик открывают и по прямой ссылке — такие замеры тоже про кого-то."""
    probe(RU, SubHostProbe.Outcome.OK, uuid="ShFz9bu699hZmcYH")
    probe(EU, SubHostProbe.Outcome.FAILED, uuid="ShFz9bu699hZmcYH")

    assert health()["split"]["only_one"] == 1


def test_probes_with_no_identity_count_in_totals_but_not_in_split():
    probe(RU, SubHostProbe.Outcome.OK)
    probe(EU, SubHostProbe.Outcome.FAILED)

    data = health()

    assert data["probes"] == 2
    assert data["split"]["checked"] == 0


def test_nobody_works_is_told_apart_from_everyone_works():
    bad = NexUserFactory()
    probe(RU, SubHostProbe.Outcome.FAILED, user=bad)
    probe(EU, SubHostProbe.Outcome.FAILED, user=bad)

    split = health()["split"]

    assert split["none"] == 1
    assert split["only_one"] == 0
    assert split["both_ok"] == 0


def test_endpoint_needs_staff(client):
    assert client.get("/dash/api/sub-probes/").status_code in (302, 403)


def test_endpoint_returns_the_block(admin_client):
    probe(RU, SubHostProbe.Outcome.OK)

    res = admin_client.get("/dash/api/sub-probes/")

    assert res.status_code == 200
    body = res.json()
    assert body["probes"] == 1
    assert body["hosts"][0]["host"] == RU


def test_dashboard_page_carries_the_block(admin_client):
    """Расчёты без блока на странице бесполезны — и наоборот."""
    html = admin_client.get("/dash/").content.decode()

    assert "Выдача ссылки" in html
    assert "/dash/api/sub-probes/" in html
    # Блок зависит от периода в шапке, значит должен перерисовываться с ним.
    assert "loadSubProbes();" in html
