"""Выключатель напоминаний.

Нужен, потому что beat отвечает сразу за три вещи: напоминания, сбор
статистики и сверку платежей. Гасить его целиком ради одного — значит заодно
остаться без страховки на случай недошедшего вебхука об оплате.
"""

import pytest

from nexvpn.models import GlobalSettings

pytestmark = pytest.mark.django_db


def test_reminders_are_on_by_default():
    assert GlobalSettings.load().reminders_enabled is True


def test_task_sends_nothing_when_switched_off(monkeypatch):
    from nexvpn import tasks

    settings = GlobalSettings.load()
    settings.reminders_enabled = False
    settings.save(update_fields=["reminders_enabled"])

    called = []
    monkeypatch.setattr("bot.main.build_bot", lambda: called.append(True))

    assert tasks.send_subscription_reminders() == {"skipped": True}
    assert not called, "бот не должен даже подниматься"


def test_other_scheduled_work_does_not_depend_on_the_switch(monkeypatch):
    """Сверка платежей и снимки обязаны идти независимо."""
    from nexvpn import tasks

    settings = GlobalSettings.load()
    settings.reminders_enabled = False
    settings.save(update_fields=["reminders_enabled"])

    monkeypatch.setattr("nexvpn.subscription.reconcile.reconcile",
                        lambda: type("R", (), {"checked": 0, "applied": 0, "still_pending": 0, "failed": 0})())
    result = tasks.reconcile_payments()

    assert "skipped" not in result
