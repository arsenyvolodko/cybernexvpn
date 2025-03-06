from django.db.models import TextChoices


class ClientUpdatesEnum(TextChoices):
    SUBSCRIPTION_STOPPED_DUE_TO_LACK_OF_FUNDS = (
        'subscription_stopped_due_to_lack_of_funds',
        'Подписка остановлена из-за отсутствия средств на счете'
    )
    SUBSCRIPTION_STOPPED_DUE_TO_OFFED_AUTO_RENEW = (
        'subscription_stopped_due_to_offed_auto_renew',
        'Подписка остановлена из-за отключенного автопродления'
    )
    RENEWED = 'renewed', 'Подписка продлена'
    DELETED = 'deleted', 'Устройство удалено'
    CREATED = 'created', 'Устройство добавлено'
