from django.db import models


class ClientTypeEnum(models.TextChoices):
    UNKNOWN = "unknown", "Неизвестен"
    ANDROID = "android", "Android"
    IOS = "iphone", "Iphone"
    PC = "pc", "PC"
