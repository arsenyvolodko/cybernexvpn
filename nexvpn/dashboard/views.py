"""Страница дашборда и её JSON-ручки.

Доступ — по обычной сессии Django-админки. Ключ `ADMIN_API_KEY` сюда сознательно
не пускаем: он умеет не только читать (создавать платежи, править людей), а
страница в браузере рано или поздно окажется в чужой истории или на чужом
экране. Права на чтение статистики не должны тянуть за собой права на запись.
"""

from __future__ import annotations

import json
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect, render

from . import periods, queries


def _staff_page(view):
    """Страница: не пустили — отправляем на логин админки."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_staff):
            return redirect(f"/admin/login/?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapper


def _staff_api(view):
    """Ручка: не пустили — отдаём JSON, а не редирект на HTML-страницу логина.

    Иначе `fetch` молча получает 200 с формой логина и падает на разборе JSON,
    и в консоли видно «Unexpected token <» вместо «сессия кончилась».
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_staff):
            return JsonResponse({"error": "Нужен вход в админку", "login": "/admin/login/"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


@_staff_page
def page(request):
    """Сама страница. Данные подтягиваются с ручек уже в браузере."""
    return render(
        request,
        "nexvpn/dashboard.html",
        {
            "presets": json.dumps(list(periods.PRESETS.keys())),
            "segments": json.dumps(queries.SEGMENTS),
            "metrics": json.dumps(queries.SERIES_METRICS),
        },
    )


@_staff_api
def overview(request):
    """Плитки и ряд для графика — одним запросом, чтобы цифры были одного среза."""
    period = periods.parse(request.GET)
    metric = request.GET.get("metric", "revenue")
    return JsonResponse(
        {
            "period": {
                "from": period.date_from.isoformat(),
                "to": period.date_to.isoformat(),
                "granularity": period.granularity,
                "days": period.days,
            },
            "cards": queries.summary(period),
            "series": queries.series(period, metric),
        }
    )


@_staff_api
def payments(request):
    period = periods.parse(request.GET)
    return JsonResponse(
        {
            "rows": queries.payments(
                period,
                day=request.GET.get("day", ""),
                only_paid=request.GET.get("all") != "1",
            )
        }
    )


@_staff_api
def users(request):
    period = periods.parse(request.GET)
    segment = request.GET.get("segment", "active")
    if segment not in queries.SEGMENTS:
        segment = "active"
    return JsonResponse(
        {
            "segment": segment,
            "title": queries.SEGMENTS[segment],
            "rows": queries.users(segment, period, search=request.GET.get("q", "").strip()),
        }
    )


@_staff_api
def tunnels(request):
    """Всё про туннели одним запросом: список, сети, операторы и динамика."""
    period = periods.parse(request.GET)
    metric = request.GET.get("tunnel_metric", "people")
    if metric not in ("people", "connections"):
        metric = "people"
    return JsonResponse(
        {
            "tunnels": queries.tunnels(period),
            "networks": queries.networks(period),
            "operators": queries.operators(period),
            "matrix": queries.operator_tunnel_matrix(period),
            "series": queries.tunnel_series(period, metric),
        }
    )


@_staff_api
def user_card(request, user_id: int):
    card = queries.user_card(user_id)
    if card is None:
        return JsonResponse({"error": "Нет такого пользователя"}, status=404)
    return JsonResponse(card)
