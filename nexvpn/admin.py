from django.contrib import admin

from nexvpn.models import NexUser, UserInvitation, ServerConfig, Server, Client, Endpoint, Payment, Transaction, \
    UserBalance

# Register your models here.
admin.site.register(NexUser)
admin.site.register(UserInvitation)
admin.site.register(ServerConfig)
admin.site.register(Server)
admin.site.register(Client)
admin.site.register(Endpoint)
admin.site.register(Payment)
admin.site.register(Transaction)
admin.site.register(UserBalance)
