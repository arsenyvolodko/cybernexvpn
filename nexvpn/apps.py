from django.apps import AppConfig


class NexvpnConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nexvpn"

    def ready(self):
        from nexvpn import signals  # noqa: F401  — подключает обработчики
