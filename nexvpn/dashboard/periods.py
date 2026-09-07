"""Период и детализация: разбор параметров запроса и раскладка по корзинам.

Всё считается в `settings.TIME_ZONE` (Москва), а не в UTC. Это не косметика:
подписки заканчиваются в 20:00 по Москве, платежи вечером 7-го числа в UTC
попадают в 7-е, а по Москве — тоже в 7-е, но вот всё, что после 21:00 МСК,
в UTC уже завтра. Без явной зоны график съезжал бы на день на четверти точек.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

# Пресеты в шапке. Значение — сколько дней назад от сегодня.
PRESETS = {
    "1": 1,
    "7": 7,
    "30": 30,
    "90": 90,
    "180": 180,
    "365": 365,
}

GRANULARITIES = {
    "day": (TruncDay, "%d.%m"),
    "week": (TruncWeek, "%d.%m"),
    "month": (TruncMonth, "%m.%Y"),
}

# Потолок числа точек. Год по дням — 365, это нормально; но кастомный период
# в десять лет по дням превратил бы страницу в кашу и запрос в долгий скан.
MAX_BUCKETS = 400


@dataclass(frozen=True)
class Period:
    """Отрезок времени с детализацией. Границы включительные по датам."""

    date_from: dt.date
    date_to: dt.date
    granularity: str

    @property
    def start(self) -> dt.datetime:
        """Начало первого дня, в локальной зоне."""
        tz = timezone.get_current_timezone()
        return timezone.make_aware(dt.datetime.combine(self.date_from, dt.time.min), tz)

    @property
    def end(self) -> dt.datetime:
        """Конец последнего дня. Правая граница исключающая — так безопаснее
        сравнивать: `__lt=end` не зависит от микросекундной точности поля."""
        tz = timezone.get_current_timezone()
        return timezone.make_aware(
            dt.datetime.combine(self.date_to + dt.timedelta(days=1), dt.time.min), tz
        )

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def previous(self) -> "Period":
        """Такой же по длине отрезок, стоящий вплотную перед этим."""
        length = dt.timedelta(days=self.days)
        return Period(self.date_from - length, self.date_from - dt.timedelta(days=1), self.granularity)

    def trunc(self, field: str):
        """Выражение округления даты для группировки — с явной зоной."""
        cls, _ = GRANULARITIES[self.granularity]
        return cls(field, tzinfo=timezone.get_current_timezone())

    def buckets(self) -> list[dt.date]:
        """Все корзины периода по порядку, включая пустые.

        График должен быть непрерывным: день без платежей — это ноль, а не
        разрыв линии. База пустые дни не вернёт, поэтому раскладку строим сами.
        """
        result: list[dt.date] = []
        cursor = self._floor(self.date_from)
        while cursor <= self.date_to:
            result.append(cursor)
            cursor = self._next(cursor)
        return result

    def _floor(self, day: dt.date) -> dt.date:
        if self.granularity == "week":
            return day - dt.timedelta(days=day.weekday())
        if self.granularity == "month":
            return day.replace(day=1)
        return day

    def _next(self, day: dt.date) -> dt.date:
        if self.granularity == "week":
            return day + dt.timedelta(days=7)
        if self.granularity == "month":
            return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return day + dt.timedelta(days=1)

    def bucket_range(self, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
        """Границы одной корзины — для провала из графика в конкретную точку.

        Для детализации по неделям и месяцам «день» на графике это начало
        корзины, и провалиться надо во всю неделю, а не в понедельник.
        """
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
        end = timezone.make_aware(dt.datetime.combine(self._next(day), dt.time.min), tz)
        return start, end

    def label(self, day: dt.date) -> str:
        _, fmt = GRANULARITIES[self.granularity]
        return day.strftime(fmt)


def parse(params) -> Period:
    """Собрать период из query-параметров. Кривой ввод не роняет страницу.

    Приоритет у явных дат: если пришли `from`/`to`, пресет игнорируется. Так
    ведёт себя и календарь в шапке — выбор дат сбрасывает пресет.
    """
    today = timezone.localdate()

    date_from = _parse_date(params.get("from"))
    date_to = _parse_date(params.get("to"))

    if date_from is None or date_to is None:
        days = PRESETS.get(str(params.get("preset", "30")), 30)
        date_to = today
        date_from = today - dt.timedelta(days=days - 1)

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    granularity = params.get("granularity", "day")
    if granularity not in GRANULARITIES:
        granularity = "day"

    period = Period(date_from, date_to, granularity)

    # Слишком мелкая детализация на длинном отрезке — молча укрупняем, а не
    # отдаём тысячу точек, которые всё равно не нарисуются различимо.
    while len(period.buckets()) > MAX_BUCKETS and granularity != "month":
        granularity = "week" if granularity == "day" else "month"
        period = Period(date_from, date_to, granularity)

    return period


def _parse_date(value) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
