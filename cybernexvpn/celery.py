import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cybernexvpn.settings')

app = Celery('cybernexvpn')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Единственная периодическая задача: догнать подписки, не доехавшие до
    # Remnawave. Панель могла лежать в момент оплаты.
    'sync-panel': {
        'task': 'nexvpn.tasks.sync_panel',
        'schedule': crontab(minute='*/5'),
    },
}
