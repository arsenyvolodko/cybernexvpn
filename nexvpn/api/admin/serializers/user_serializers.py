from rest_framework import serializers

from nexvpn.models import NexUser


class NexUserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(allow_null=True)
    first_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    is_activated = serializers.BooleanField(read_only=True)

    class Meta:
        model = NexUser
        fields = ("id", "username", "first_name", "email", "token", "is_legacy", "is_activated", "activated_at")
        read_only_fields = ("id", "token", "is_legacy", "is_activated", "activated_at")


class NexUserUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(allow_null=True, required=False)
    first_name = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    email = serializers.EmailField(allow_null=True, allow_blank=True, required=False)

    class Meta:
        model = NexUser
        fields = ("username", "first_name", "email")
