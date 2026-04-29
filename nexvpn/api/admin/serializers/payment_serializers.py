from rest_framework import serializers


class PaymentRequestSerializers(serializers.Serializer):
    value = serializers.IntegerField()
    return_url = serializers.URLField(required=False, allow_null=True, default=None)
    email = serializers.EmailField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        value = attrs.get('value')
        if value <= 0:
            raise serializers.ValidationError('Value must be positive')
        return attrs

    class Meta:
        fields = ('value', 'return_url', 'email')


class PaymentResponseSerializer(serializers.Serializer):
    url = serializers.URLField()

    class Meta:
        fields = ('url', )
