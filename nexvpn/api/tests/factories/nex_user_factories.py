from factory import Faker
from factory.django import DjangoModelFactory

from nexvpn.models import NexUser


class NexUserFactory(DjangoModelFactory):
    username = Faker("user_name")

    class Meta:
        model = NexUser
