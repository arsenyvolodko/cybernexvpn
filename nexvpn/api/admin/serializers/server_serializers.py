from rest_framework import serializers

from nexvpn.models import Server


class ServerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Server
        fields = ["id", "name", "price", ]
