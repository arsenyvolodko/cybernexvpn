import os
from celery import Celery
from celery.schedules import crontab
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils.timezone import now

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cybernexvpn.settings')

app = Celery('cybernexvpn')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

reminder_time = settings.SEND_UPDATES_REMINDER_TIME.split(":")
updates_time = settings.SEND_UPDATES_TIME.split(":")

app.conf.beat_schedule = {
    'send-subscription-reminders': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour=reminder_time[0], minute=reminder_time[1]),
        'args': [False, True],
    },
    'make-subscription-updates': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour=updates_time[0], minute=updates_time[1]),
        'args': [True, False],
    },
}
