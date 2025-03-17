from factory import Faker, SubFactory
from factory.django import DjangoModelFactory
from wireguard_tools import WireguardKey

from nexvpn.models import ServerConfig, Server


class ServerConfigFactory(DjangoModelFactory):
    ip = Faker("ipv4")
    api_key = Faker("md5")
    api_port = Faker("random_int", min=1024, max=65535)
    wg_address = Faker("ipv4")
    wg_listen_port = Faker("random_int", min=1024, max=65535)
    wg_private_key = WireguardKey.generate()
    ssl = Faker("boolean")

    class Meta:
        model = ServerConfig


class ServerFactory(DjangoModelFactory):
    name = Faker("name")
    price = Faker("random_int", min=10, max=200)
    is_active = True
    config = SubFactory(ServerConfigFactory)
    tag = Faker("name")

    class Meta:
        model = Server
