from factory import Faker
from factory.django import DjangoModelFactory

from nexvpn.models import NexUser, UserBalance


class NexUserFactory(DjangoModelFactory):
    username = Faker("user_name")

    class Meta:
        model = NexUser


class UserBalanceFactory(DjangoModelFactory):
    user = Faker("user_name")
    value = Faker("random_int")

    class Meta:
        model = UserBalance
