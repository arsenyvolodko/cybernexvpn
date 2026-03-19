import secrets

from nexvpn.models import ProxyClient


def generate_unique_secret():
    while True:
        secret = secrets.token_hex(16)
        if not ProxyClient.objects.filter(secret=secret).exists():
            return secret
