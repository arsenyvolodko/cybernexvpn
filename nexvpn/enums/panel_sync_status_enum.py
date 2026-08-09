from django.db import models


class PanelSyncStatusEnum(models.TextChoices):
    NEVER_SYNCED = "never_synced", "Ни разу не синхронизирована"
    SYNCED = "synced", "Синхронизирована"
    PENDING = "pending", "Ожидает синхронизации"
    FAILED = "failed", "Ошибка синхронизации"
