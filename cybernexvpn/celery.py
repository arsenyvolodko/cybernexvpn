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

app.conf.beat_schedule = {
    'send-subscription-reminders': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour=settings.SEND_UPDATES_REMINDER_HOUR, minute="00"),
        'args': [(now() + relativedelta(days=1)).isoformat(), True],
    },
    'make-subscription-updates': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour=settings.SEND_UPDATES_HOUR, minute="00"),
        'args': [now().isoformat(), False],
    },
}
