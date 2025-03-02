from rest_framework import serializers


class PaymentRequestSerializers(serializers.Serializer):
    value = serializers.IntegerField()

    def validate(self, attrs):
        value = attrs.get('value')
        if value <= 0:
            raise serializers.ValidationError('Value must be positive')
        return attrs

    class Meta:
        fields = ('value', )


class PaymentResponseSerializer(serializers.Serializer):
    url = serializers.URLField()

    class Meta:
        fields = ('url', )
