"""Импорт celery-приложения при старте Django.

Без этого `shared_task(...).delay()` из веб-процесса резолвится в дефолтное
celery-приложение с брокером `amqp://localhost` и молча теряется: worker
поднимается с явным `-A cybernexvpn.celery.app`, а wsgi — нет.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
