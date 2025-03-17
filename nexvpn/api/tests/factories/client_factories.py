from factory import Faker, SubFactory, LazyAttribute
from factory.django import DjangoModelFactory
from wireguard_tools import WireguardKey

from nexvpn.api.tests.conftest import fake
from nexvpn.api.tests.factories import ServerFactory, NexUserFactory
from nexvpn.enums import ClientTypeEnum
from nexvpn.models import Client


class ClientFactory(DjangoModelFactory):
    user = SubFactory(NexUserFactory)
    name = Faker("name")
    is_active = True
    auto_renew = True
    end_date = Faker("future_date")
    num = Faker("random_int")
    type = LazyAttribute(
        lambda _: fake.random_element(ClientTypeEnum.values)
    )
    server = SubFactory(ServerFactory)
    private_key = WireguardKey.generate()

    class Meta:
        model = Client
