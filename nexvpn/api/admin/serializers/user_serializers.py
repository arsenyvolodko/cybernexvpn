from rest_framework import serializers

from nexvpn.models import NexUser


class NexUserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(allow_null=True)
    balance = serializers.IntegerField(read_only=True)

    class Meta:
        model = NexUser
        fields = ('id', 'username', 'balance', )
