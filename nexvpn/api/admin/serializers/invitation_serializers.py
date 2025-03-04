from rest_framework import serializers


class InvitationRequestSerializer(serializers.Serializer):
    inviter = serializers.IntegerField()

    class Meta:
        fields = ("inviter", )
