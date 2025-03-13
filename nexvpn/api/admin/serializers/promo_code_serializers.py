from rest_framework import serializers


class PromoCodeRequestSerializer(serializers.Serializer):
    code = serializers.CharField()

    class Meta:
        fields = ("code",)


class PromoCodeResponseSerializer(serializers.Serializer):
    value = serializers.IntegerField()

    class Meta:
        fields = ("value",)
