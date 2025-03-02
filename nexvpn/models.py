from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser

from django.db import models
from django.db.models import Model
from django.utils.timezone import now
from wireguard_tools import WireguardKey

from nexvpn.enums import TransactionTypeEnum, ClientTypeEnum, TransactionStatusEnum

User = get_user_model()


class NexUser(models.Model):
    username = models.CharField(max_length=63, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.OneToOneField(User, null=True, default=None, on_delete=models.SET_NULL, blank=True)

    @property
    def balance(self):
        return UserBalance.objects.get(user=self).value

    def __str__(self):
        return self.username or str(self.user_id)


class UserInvitation(models.Model):
    inviter = models.ForeignKey(
        NexUser, on_delete=models.CASCADE
    )
    invitee = models.OneToOneField(
        NexUser, on_delete=models.CASCADE, related_name="invitation"
    )


class ServerConfig(models.Model):
    ip = models.CharField(max_length=15, unique=True)
    api_key = models.CharField(max_length=63, unique=True)
    api_port = models.IntegerField(default=8080)
    ssl = models.BooleanField(default=False)

    wg_address = models.CharField(max_length=17)
    wg_listen_port = models.IntegerField(default=51820)
    wg_private_key = models.CharField(max_length=63)

    @property
    def base_url(self) -> str:
        protocol = "https" if self.ssl else "http"
        return f"{protocol}://{self.ip}:{self.api_port}/api/v1"

    @property
    def wg_public_key(self) -> str:
        # noinspection PyTypeChecker
        return str(WireguardKey(self.wg_private_key).public_key())


class Server(models.Model):
    name = models.CharField(max_length=31)
    price = models.IntegerField(default=settings.DEFAULT_SUBSCRIPTION_PRICE)
    is_active = models.BooleanField(default=True)
    config = models.OneToOneField(ServerConfig, on_delete=models.CASCADE)


class BaseClient(models.Model):
    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True)
    auto_renew = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    end_date = models.DateField()
    num = models.IntegerField()
    type = models.CharField(max_length=31, choices=ClientTypeEnum, default=ClientTypeEnum.UNKNOWN)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "num"], name="unique_user_num"
            )
        ]

        abstract = True


class Client(BaseClient):
    server = models.ForeignKey(Server, on_delete=models.CASCADE)
    private_key = models.CharField(max_length=63)

    @property
    def config_name(self) -> str:
        return f"{self.name}_{self.num}"

    @property
    def public_key(self) -> str:
        # noinspection PyTypeChecker
        return str(WireguardKey(self.private_key).public_key())

    def save(self, *args, **kwargs):
        if not self.pk:
            self.end_date = (now() + relativedelta(months=1)).date()
            user_id = self.user_id
            max_client = Client.objects.filter(user_id=user_id).order_by("-num").first()
            self.num = max_client.num + 1 if max_client else 1
            self.name = self.name or f"Устройство №{self.num}"
            if not self.private_key:
                self.private_key = str(WireguardKey.generate())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.num}"


class Endpoint(models.Model):
    server = models.ForeignKey(Server, on_delete=models.CASCADE)
    ip = models.CharField(max_length=17)
    client = models.OneToOneField(Client, on_delete=models.SET_NULL, null=True, default=None, related_name="endpoint", blank=True)

    def __str__(self):
        return f"{self.client} - {self.server.name}: {self.ip}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["server", "ip"], name="unique_server_ip"
            )
        ]


class Payment(models.Model):
    uuid = models.UUIDField(primary_key=True)
    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)


class Transaction(models.Model):
    user = models.ForeignKey(NexUser, on_delete=models.CASCADE)
    is_credit = models.BooleanField()
    value = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, null=True, default=None)
    type = models.CharField(max_length=31, choices=TransactionTypeEnum.choices)
    status = models.CharField(
        max_length=31,
        choices=TransactionStatusEnum.choices,
        default=TransactionStatusEnum.SUCCEEDED,
    )

    def __str__(self):
        timestamp = self.created_at.strftime(format="%d.%m.%Y %H:%M:%S")
        credit_type = "Пополнение" if self.is_credit else "Списание"
        status, type_ = self.get_status_display(), self.get_type_display()
        return f"{timestamp}: {credit_type} в размере {self.value}₽ - [{status}] - {type_}."


class UserBalance(models.Model):
    user = models.OneToOneField(NexUser, on_delete=models.CASCADE)
    value = models.IntegerField(default=settings.START_BALANCE)

    def __str__(self):
        return f"{self.user.username or self.user_id}: {self.value}₽"

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(value__gte=0), name="value_gte_0")
        ]
