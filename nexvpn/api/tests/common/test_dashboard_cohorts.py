"""Когорты и активность по дням.

Проверяется смысл, а не вёрстка. Главное здесь — границы: когорта определяется
неделей регистрации по московскому времени, и человек, зашедший в воскресенье
в 23:30, обязан попасть в прошлую неделю, а не в ту, что начнётся через
полчаса. Второе — что пустая когорта не превращается в ноль: «никто не купил»
и «покупать было некому» — разные вещи, и вторая должна быть прочерком.
"""

import datetime as dt
import json
import uuid

import pytest
from django.utils import timezone

from nexvpn.api.tests.factories import NexUserFactory, PlanFactory, SubscriptionFactory
from nexvpn.dashboard import periods, queries
from nexvpn.enums.subscription_event_reason_enum import SubscriptionEventReasonEnum as Reason
from nexvpn.models import NexUser, NodeUsageDay, PanelPresence, Payment, SubscriptionEvent

pytestmark = pytest.mark.django_db


# --- вспомогательное ---


def local(day: dt.date, hour: int = 12, minute: int = 0) -> dt.datetime:
    """Момент в зоне сервиса — когорты считаются по ней, а не по UTC."""
    return timezone.make_aware(
        dt.datetime.combine(day, dt.time(hour, minute)), timezone.get_current_timezone()
    )


def this_monday() -> dt.date:
    today = timezone.localdate()
    return today - dt.timedelta(days=today.weekday())


def user_at(moment: dt.datetime, **kwargs) -> NexUser:
    """`created_at` — auto_now_add, задним числом его ставит только UPDATE."""
    user = NexUserFactory(**kwargs)
    NexUser.objects.filter(pk=user.pk).update(created_at=moment)
    user.created_at = moment
    return user


def event(user: NexUser, reason: str, moment: dt.datetime) -> None:
    record = SubscriptionEvent.objects.create(user=user, reason=reason)
    SubscriptionEvent.objects.filter(pk=record.pk).update(created_at=moment)


def paid(user: NexUser, amount: int, moment: dt.datetime) -> None:
    Payment.objects.create(
        uuid=uuid.uuid4(), idempotence_key=uuid.uuid4(), user=user,
        amount=amount, processed_at=moment,
    )


def week(rows: list[dict], monday: dt.date) -> dict:
    return next(row for row in rows if row["week"] == monday.isoformat())


# --- границы недель ---


def test_a_cohort_is_the_week_of_registration_in_local_time():
    """Полчаса до понедельника — это ещё прошлая неделя."""
    monday = this_monday()
    user_at(local(monday - dt.timedelta(days=1), 23, 30))
    user_at(local(monday, 0, 30))

    rows = queries.cohorts(weeks=2)

    assert [row["week"] for row in rows] == [
        monday.isoformat(), (monday - dt.timedelta(days=7)).isoformat()
    ], "недели идут от свежей к старой"
    assert week(rows, monday)["users"] == 1, "полночь понедельника — уже новая когорта"
    assert week(rows, monday - dt.timedelta(days=7))["users"] == 1, "вечер воскресенья — старая"


def test_the_running_week_is_marked_as_incomplete():
    """Текущую неделю показываем, но подписываем: её цифры ещё неполные."""
    rows = queries.cohorts(weeks=4)

    assert rows[0]["partial"] is True
    assert all(row["partial"] is False for row in rows[1:])


def test_older_registrations_do_not_leak_into_the_shown_weeks():
    monday = this_monday()
    user_at(local(monday - dt.timedelta(days=30)))

    rows = queries.cohorts(weeks=2)

    assert sum(row["users"] for row in rows) == 0


# --- воронка когорты ---


def test_a_purchase_after_the_trial_counts_as_a_conversion():
    monday = this_monday()
    converted = user_at(local(monday, 9))
    event(converted, Reason.TRIAL, local(monday, 10))
    event(converted, Reason.PURCHASE, local(monday, 11))
    just_trying = user_at(local(monday, 9))
    event(just_trying, Reason.TRIAL, local(monday, 10))

    row = week(queries.cohorts(weeks=1), monday)

    assert row["trials"] == 2 and row["paid"] == 1
    assert row["conversion"] == 50


def test_a_purchase_before_the_trial_is_not_a_conversion():
    """Легаси переносили с уже оплаченными днями, а пробный им выдали позже.
    По первой покупке такой человек выглядел бы как конверсия, которой не было."""
    monday = this_monday()
    legacy = user_at(local(monday, 9))
    event(legacy, Reason.PURCHASE, local(monday, 10))
    event(legacy, Reason.TRIAL, local(monday, 11))

    row = week(queries.cohorts(weeks=1), monday)

    assert row["trials"] == 1 and row["paid"] == 0
    assert row["conversion"] == 0


def test_a_cohort_without_trials_has_no_conversion_at_all():
    """Ноль процентов и «считать не от чего» — разные вещи, и рисуются по-разному."""
    monday = this_monday()
    user_at(local(monday, 9))

    row = week(queries.cohorts(weeks=1), monday)

    assert row["trials"] == 0
    assert row["conversion"] is None


# --- деньги когорты ---


def test_money_of_a_cohort_is_counted_over_its_whole_life():
    monday = this_monday()
    big = user_at(local(monday, 9))
    paid(big, 400, local(monday, 10))
    paid(big, 400, local(monday + dt.timedelta(days=3), 10))
    small = user_at(local(monday, 9))
    paid(small, 200, local(monday + dt.timedelta(days=1), 10))
    user_at(local(monday, 9))  # не заплатил — портит ARPU, но не ARPPU

    row = week(queries.cohorts(weeks=1), monday)

    assert row["revenue"] == 1000
    assert row["payments"] == 3 and row["payers"] == 2
    assert row["arppu"] == 500, "выручка на заплатившего"
    assert row["avg_check"] == 333, "выручка на платёж"
    assert row["repeat_share"] == 50, "один из двух заплатил повторно"
    assert row["days_to_pay"] == 0, "медиана из 0 и 1 дня"


def test_an_unpaid_payment_brings_no_money():
    monday = this_monday()
    user = user_at(local(monday, 9))
    Payment.objects.create(
        uuid=uuid.uuid4(), idempotence_key=uuid.uuid4(), user=user, amount=400,
    )

    row = week(queries.cohorts(weeks=1), monday)

    assert row["revenue"] == 0 and row["payers"] == 0
    assert row["arppu"] is None and row["avg_check"] is None
    assert row["days_to_pay"] is None and row["repeat_share"] is None


# --- удержание ---


def test_retention_counts_live_subscriptions_and_recent_online():
    monday = this_monday()
    moment = timezone.now()
    plan = PlanFactory(device_limit=3, price_month=400)

    alive = SubscriptionFactory(plan=plan, expires_at=moment + dt.timedelta(days=10)).user
    NexUser.objects.filter(pk=alive.pk).update(created_at=local(monday, 9))
    PanelPresence.objects.create(
        user=alive, first_connected_at=local(monday, 10), online_at=moment - dt.timedelta(hours=2)
    )

    gone = SubscriptionFactory(plan=plan, expires_at=moment - dt.timedelta(days=1)).user
    NexUser.objects.filter(pk=gone.pk).update(created_at=local(monday, 9))
    PanelPresence.objects.create(
        user=gone, first_connected_at=local(monday, 10), online_at=moment - dt.timedelta(days=30)
    )

    never = SubscriptionFactory(plan=plan, expires_at=moment + dt.timedelta(days=10)).user
    NexUser.objects.filter(pk=never.pk).update(created_at=local(monday, 9))

    row = week(queries.cohorts(weeks=1), monday)

    assert row["users"] == 3
    assert row["connected"] == 2 and row["connected_share"] == 67
    assert row["active"] == 1, "живая подписка И хоть раз подключался"
    assert row["active_share"] == 33
    assert row["online"] == 1 and row["online_share"] == 33


# --- пустые данные и объём запросов ---


def test_an_empty_database_still_gives_a_full_table():
    rows = queries.cohorts(weeks=12)

    assert len(rows) == 12
    assert all(row["users"] == 0 for row in rows)
    assert all(row["conversion"] is None and row["arppu"] is None for row in rows)
    assert all(row["label"] for row in rows), "подпись недели есть всегда"


def test_the_number_of_weeks_is_clamped():
    assert len(queries.cohorts(weeks=0)) == 1
    assert len(queries.cohorts(weeks=500)) == 52


def test_the_whole_table_takes_a_fixed_number_of_queries(django_assert_max_num_queries):
    """Запрос на когорту в цикле — самый простой способ положить страницу."""
    monday = this_monday()
    for offset in range(8):
        for _ in range(3):
            person = user_at(local(monday - dt.timedelta(days=7 * offset), 9))
            event(person, Reason.TRIAL, local(monday - dt.timedelta(days=7 * offset), 10))
            paid(person, 400, local(monday - dt.timedelta(days=7 * offset), 11))

    with django_assert_max_num_queries(3):
        queries.cohorts(weeks=12)


# --- активность по дням ---


def usage(user: NexUser, day: dt.date, node: str = "de1-ovh", size: int = 1024) -> None:
    NodeUsageDay.objects.create(user=user, node_name=node, date=day, bytes=size)


def period_of(days: int) -> periods.Period:
    today = timezone.localdate()
    return periods.Period(today - dt.timedelta(days=days - 1), today, "day")


def test_active_users_per_day_count_people_once():
    """Человек за сутки бывает на двух нодах — это две строки и один человек."""
    today = timezone.localdate()
    user = NexUserFactory()
    usage(user, today, node="de1-ovh")
    usage(user, today, node="nl1-ovh")
    usage(NexUserFactory(), today - dt.timedelta(days=1))

    series = queries.activity_series(period_of(3), "active")

    assert series["values"] == [0, 1, 1]
    assert series["kind"] == "line" and series["format"] == "int"


def test_traffic_per_day_and_per_user():
    today = timezone.localdate()
    first, second = NexUserFactory(), NexUserFactory()
    usage(first, today, size=300)
    usage(second, today, size=100)

    traffic = queries.activity_series(period_of(2), "traffic")
    per_user = queries.activity_series(period_of(2), "traffic_per_user")

    assert traffic["values"] == [0, 400] and traffic["format"] == "bytes"
    assert per_user["values"] == [0, 200], "день без активных — ноль, а не деление на ноль"


def test_first_connections_are_counted_on_the_day_they_happened():
    today = timezone.localdate()
    yesterday = today - dt.timedelta(days=1)
    PanelPresence.objects.create(user=NexUserFactory(), first_connected_at=local(yesterday, 23))
    PanelPresence.objects.create(user=NexUserFactory(), first_connected_at=local(today, 1))
    PanelPresence.objects.create(user=NexUserFactory(), first_connected_at=None)

    series = queries.activity_series(period_of(2), "new_connected")

    assert series["values"] == [1, 1]


def test_silence_is_counted_among_those_who_used_it_recently():
    """Молчит тот, кто на прошлой неделе выходил, а за эти сутки — нет."""
    today = timezone.localdate()
    quiet = NexUserFactory()
    usage(quiet, today - dt.timedelta(days=2))
    still_here = NexUserFactory()
    usage(still_here, today - dt.timedelta(days=2))
    usage(still_here, today)
    NexUserFactory()  # ни разу не подключался — он не «замолчал»

    series = queries.activity_series(period_of(1), "silent")

    assert series["values"] == [1]


def test_series_of_an_empty_database_are_zeroes_not_gaps():
    for metric in queries.ACTIVITY_METRICS:
        series = queries.activity_series(period_of(5), metric)
        assert series["values"] == [0, 0, 0, 0, 0], metric
        assert len(series["labels"]) == 5 and series["hint"]


def test_an_unknown_metric_falls_back_instead_of_failing():
    series = queries.activity_series(period_of(3), "чепуха")

    assert series["metric"] == "active"


def test_a_long_range_is_clipped_to_days_and_says_so():
    """Ряд всегда по суткам: «активных за неделю» сложением дней не получить."""
    today = timezone.localdate()
    long_range = periods.Period(today - dt.timedelta(days=900), today, "month")

    series = queries.activity_series(long_range, "active")

    assert series["clipped"] is True
    assert len(series["values"]) == periods.MAX_BUCKETS
    assert series["to"] == today.isoformat()


def test_activity_cards_survive_an_empty_period():
    cards = queries.activity_cards(period_of(7))

    assert [card["key"] for card in cards] == ["unique", "dau", "traffic", "silent"]
    assert all(card["value"] == 0 for card in cards)
    assert all(card["hint"] for card in cards)


def test_activity_cards_count_people_and_bytes_over_the_period():
    today = timezone.localdate()
    user = NexUserFactory()
    usage(user, today, size=500)
    usage(user, today - dt.timedelta(days=1), size=500)

    cards = {card["key"]: card["value"] for card in queries.activity_cards(period_of(2))}

    assert cards["unique"] == 1, "человек считается один раз на весь период"
    assert cards["traffic"] == 1000
    assert cards["dau"] == 1, "активен оба дня периода"


def test_the_silent_card_looks_at_live_subscriptions_only():
    moment = timezone.now()
    plan = PlanFactory(device_limit=3, price_month=400)
    quiet = SubscriptionFactory(plan=plan, expires_at=moment + dt.timedelta(days=5)).user
    PanelPresence.objects.create(
        user=quiet, first_connected_at=moment - dt.timedelta(days=10),
        online_at=moment - dt.timedelta(days=3),
    )
    expired = SubscriptionFactory(plan=plan, expires_at=moment - dt.timedelta(days=5)).user
    PanelPresence.objects.create(
        user=expired, first_connected_at=moment - dt.timedelta(days=10),
        online_at=moment - dt.timedelta(days=3),
    )
    online = SubscriptionFactory(plan=plan, expires_at=moment + dt.timedelta(days=5)).user
    PanelPresence.objects.create(
        user=online, first_connected_at=moment - dt.timedelta(days=10),
        online_at=moment - dt.timedelta(hours=1),
    )

    cards = {card["key"]: card["value"] for card in queries.activity_cards(period_of(7))}

    assert cards["silent"] == 1


# --- ручка и страница ---


def test_the_cohorts_api_answers_staff_and_refuses_everyone_else(admin_client, client):
    response = admin_client.get("/dash/api/cohorts/", {"weeks": "8", "activity_metric": "traffic"})
    assert response.status_code == 200
    body = response.json()
    assert body["weeks"] == 8 and len(body["rows"]) == 8
    assert body["series"]["metric"] == "traffic"
    assert len(body["cards"]) == 4

    assert client.get("/dash/api/cohorts/").status_code == 403


def test_a_broken_weeks_parameter_does_not_break_the_handler(admin_client):
    response = admin_client.get("/dash/api/cohorts/", {"weeks": "много"})

    assert response.status_code == 200
    assert response.json()["weeks"] == queries.DEFAULT_COHORT_WEEKS


def test_the_page_has_the_cohorts_section(admin_client):
    page = admin_client.get("/dash/").content.decode()

    assert "Когорты по неделям регистрации" in page
    assert "Активность по дням" in page
    assert 'id="cohortTable"' in page and 'id="activityChart"' in page
    # Названия и подсказки метрик уезжают на фронт одним JSON — чипы строятся
    # из него, и если он не доехал, раздел будет пустым.
    assert json.dumps(queries.ACTIVITY_METRICS) in page
    assert json.dumps(queries.COHORT_WEEK_CHOICES) in page


def test_the_page_script_is_valid_javascript(admin_client, tmp_path):
    """Шаблон собирается руками, без сборки — синтаксис проверить больше нечем."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")

    page = admin_client.get("/dash/").content.decode()
    script = page.split("<script>")[1].split("</script>")[0]
    assert "{{" not in script, "в отрендеренной странице не должно остаться тегов шаблона"

    path = tmp_path / "dashboard.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
