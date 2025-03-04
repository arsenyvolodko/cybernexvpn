from django.db.models import TextChoices


class SubscriptionUpdateStatusEnum(TextChoices):
    STOPPED_DUE_TO_LACK_OF_FUNDS = 'stopped_due_to_lack_of_funds', 'Остановлена из-за отсутствия средств на счете'
    STOPPED_DUE_TO_OFFED_AUTO_RENEW = 'stopped_due_to_offed_auto_renew', 'Остановлена из-за отключенного автопродления'
    RENEWED = 'renewed', 'Продлена'
    DELETED = 'deleted', 'Устройство удалено'
    REACTIVATED = 'reactivated', 'Возобновлена'
