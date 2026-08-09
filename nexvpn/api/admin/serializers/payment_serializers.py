from rest_framework import serializers

from nexvpn.enums import PaymentKindEnum


class PaymentRequestSerializer(serializers.Serializer):
    """Сумму считает сервер. Клиент говорит только, что он покупает."""

    kind = serializers.ChoiceField(choices=PaymentKindEnum.choices, default=PaymentKindEnum.PURCHASE)
    device_limit = serializers.IntegerField(min_value=1)
    return_url = serializers.URLField(required=False, allow_null=True, default=None)
    email = serializers.EmailField(required=False, allow_null=True, default=None)

    class Meta:
        fields = ("kind", "device_limit", "return_url", "email")


class PaymentResponseSerializer(serializers.Serializer):
    url = serializers.URLField()
    amount = serializers.IntegerField()
    payment_id = serializers.CharField()

    class Meta:
        fields = ("url", "amount", "payment_id")


class PlanChangeQuoteSerializer(serializers.Serializer):
    is_upgrade = serializers.BooleanField()
    days_left = serializers.IntegerField()
    price_from = serializers.IntegerField()
    price_to = serializers.IntegerField()
    converted_days = serializers.IntegerField()
    topup_price = serializers.IntegerField(allow_null=True)
    topup_days = serializers.IntegerField(allow_null=True)
