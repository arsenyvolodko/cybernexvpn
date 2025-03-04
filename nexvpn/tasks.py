from datetime import datetime

from celery import shared_task

from nexvpn.subscription.updates import get_updates_schema


@shared_task()
def send_updates(date_time: datetime, is_reminder: bool):
    updates_schema = get_updates_schema(date_time, is_reminder)
    # todo: send reminders to bot
