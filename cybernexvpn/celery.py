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
    # Напоминания об окончании подписки. Раз в 10 минут достаточно: самое
    # частое смещение — «за час», такая точность человеку незаметна.
    'subscription-reminders': {
        'task': 'nexvpn.tasks.send_subscription_reminders',
        'schedule': crontab(minute='*/10'),
    },
    # Срез использования. Панель хранит только «последнее» значение счётчиков,
    # историю не отдаёт — копим сами. Шаг в десять минут задаёт и точность,
    # с которой прирост трафика приписывается ноде.
    'usage-snapshot': {
        'task': 'nexvpn.tasks.take_usage_snapshot',
        'schedule': crontab(minute='*/10'),
    },
}
