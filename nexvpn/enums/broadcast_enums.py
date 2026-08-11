from django.db import models


class BroadcastAudienceEnum(models.TextChoices):
    """Кому уходит рассылка.

    Две легаси-группы разделены потому, что им нужно рассказывать разное:
    ушедшим — что мы вернулись и подарили дни, оставшимся — что их подписка
    перенесена и ничего не потерялось.
    """

    ALL = "all", "Все пользователи"
    LEGACY_GIFTED = "legacy_gifted", "Легаси: без устройств, подарили дни"
    LEGACY_CONVERTED = "legacy_converted", "Легаси: подписка пересчитана"
    NEW = "new", "Пришли уже в новую версию"


class BroadcastStatusEnum(models.TextChoices):
    DRAFT = "draft", "Черновик"
    SENDING = "sending", "Отправляется"
    SENT = "sent", "Отправлена"
    FAILED = "failed", "Сорвалась"
