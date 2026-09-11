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
    # Страховка на случай недоставленного вебхука от ЮKassa: минута задержки
    # с начислением терпима, потерянная оплата — нет.
    'reconcile-payments': {
        'task': 'nexvpn.tasks.reconcile_payments',
        'schedule': crontab(minute='*'),
    },
    'usage-snapshot': {
        'task': 'nexvpn.tasks.take_usage_snapshot',
        'schedule': crontab(minute='*/10'),
    },
    # Подталкивания новичкам. Раз в две минуты, а не в десять: первый шаг —
    # это сама 10-я минута, на десятиминутной сетке он приходил бы на 20-й.
    # Проход стоит один запрос в базу; в панель задача идёт только за тем
    # человеком, которому прямо сейчас пора отправлять.
    'onboarding-nudges': {
        'task': 'nexvpn.tasks.send_onboarding_nudges',
        'schedule': crontab(minute='*/2'),
    },
    # 'enforce-device-limits' СНЯТА С РАСПИСАНИЯ 10.09.2026 по прямому
    # требованию владельца сразу после включения: "раз в 10 минут опрашивать
    # это хуйня какая-то, убери срочно". Функция (nexvpn.tasks.
    # enforce_device_limits, panel_sync.reject_devices_added_over_limit) и
    # тесты остались нетронуты — снята только автоматическая периодичность,
    # ничего не удалено. Включать обратно только по новому явному решению
    # владельца, не по своей инициативе.
}
