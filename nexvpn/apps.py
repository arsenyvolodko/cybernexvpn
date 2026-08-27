from django.apps import AppConfig


class NexvpnConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nexvpn"

    def ready(self):
        from django.conf import settings

        from nexvpn import signals  # noqa: F401  — подключает обработчики
        from nexvpn import yookassa_client

        # До первого обращения к ЮKassa: у SDK нет таймаута запроса, и без
        # этого недоступная касса вешает обработчик бота на десятки минут.
        yookassa_client.install(settings.YOOKASSA_TIMEOUT)
