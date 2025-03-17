from django.db import models


class ClientTypeEnum(models.TextChoices):
    UNKNOWN = "unknown", "Неизвестен"
    ANDROID = "android", "Android"
    IOS = "iphone", "Iphone"
    WINDOWS = "windows", "Windows"
    MAXOS = "macos", "MacOS"
