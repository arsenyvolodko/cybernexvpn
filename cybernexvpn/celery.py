import os
from celery import Celery
from celery.schedules import crontab
from django.utils.timezone import now

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cybernexvpn.settings')

app = Celery('cybernexvpn')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'send-subscription-reminders': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour="10", minute="00"),
        'args': (now(), True),
    },
    'make-subscription-updates': {
        'task': 'nexvpn.tasks.send_updates',
        'schedule': crontab(hour="03", minute="00"),
        'args': (now(), False),
    },
}
