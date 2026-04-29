from rest_framework import serializers

from nexvpn.models import NexUser


class NexUserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(allow_null=True)
    first_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    balance = serializers.IntegerField(read_only=True)

    class Meta:
        model = NexUser
        fields = ('id', 'username', 'first_name', 'email', 'balance', 'token')
        read_only_fields = ('id', 'balance', 'token')


class NexUserUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(allow_null=True, required=False)
    first_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)

    class Meta:
        model = NexUser
        fields = ('username', 'first_name', 'email')
