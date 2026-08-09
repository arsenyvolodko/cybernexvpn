from rest_framework import serializers

from nexvpn.models import Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ("device_limit", "price_month", "name", "is_active", "order")


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    next_plan = PlanSerializer(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    days_left = serializers.IntegerField(read_only=True)
    device_limit = serializers.IntegerField(read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "plan", "next_plan", "expires_at", "is_active", "days_left",
            "device_limit", "subscription_url", "panel_status",
        )


class DeviceSerializer(serializers.Serializer):
    hwid = serializers.CharField()
    platform = serializers.CharField(allow_null=True, required=False)
    os_version = serializers.CharField(source="osVersion", allow_null=True, required=False)
    device_model = serializers.CharField(source="deviceModel", allow_null=True, required=False)
    created_at = serializers.CharField(source="createdAt", allow_null=True, required=False)
    updated_at = serializers.CharField(source="updatedAt", allow_null=True, required=False)


class PlanChangeRequestSerializer(serializers.Serializer):
    device_limit = serializers.IntegerField(min_value=1)
