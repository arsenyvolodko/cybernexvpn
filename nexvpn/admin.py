from django.contrib import admin

from nexvpn.models import (
    AllowedUserPromoCode,
    GlobalSettings,
    LegacyMigrationRecord,
    NexUser,
    Payment,
    Plan,
    PromoCode,
    Subscription,
    SubscriptionEvent,
    Transaction,
    UsedPromoCode,
    UserInvitation,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Цены правятся здесь. Уже начисленные дни при этом не пересчитываются:
    в SubscriptionEvent лежит снимок цены на момент операции."""

    list_display = ("name", "device_limit", "price_month", "is_active", "is_public", "order")
    list_editable = ("price_month", "is_active", "is_public", "order")


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    """Синглтон: строка одна, добавить или удалить её нельзя."""

    fieldsets = (
        ("Технические работы", {
            "fields": ("maintenance_mode", "maintenance_message", "maintenance_until"),
            "description": "Включённый режим переводит бота на заглушку. Приём платежей "
                           "и синхронизацию с панелью не трогает: оплаченное доедет.",
        }),
        ("Чек 54-ФЗ", {
            "fields": ("receipt_enabled", "vat_code", "payment_subject", "payment_mode"),
            "description": "Чек включать после того, как касса подключена в личном кабинете "
                           "YooKassa. Самозанятому (НПД) нужна ставка «без НДС».",
        }),
        ("Формулировки", {
            "fields": ("payment_description", "purchase_item_template", "plan_change_item_template"),
            "description": "Эти строки видит клиент — в чеке и в выписке по карте. "
                           "Слова «VPN» в них быть не должно.",
        }),
    )
    readonly_fields = ()

    def has_add_permission(self, request):
        return not GlobalSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Сразу открывать саму настройку, а не список из одной строки."""
        from django.shortcuts import redirect
        from django.urls import reverse

        obj = GlobalSettings.load()
        return redirect(reverse("admin:nexvpn_globalsettings_change", args=[obj.pk]))


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "next_plan", "expires_at", "days_left", "panel_status")
    list_filter = ("plan", "panel_status")
    search_fields = ("user__username", "user__id")
    autocomplete_fields = ("user",)
    readonly_fields = ("panel_user_id", "panel_short_uuid", "subscription_url", "panel_synced_at", "panel_error")

    @admin.display(description="Осталось дней")
    def days_left(self, obj):
        return obj.days_left


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "reason", "delta_days", "plan", "amount")
    list_filter = ("reason",)
    search_fields = ("user__username", "user__id")


@admin.register(NexUser)
class NexUserAdmin(admin.ModelAdmin):
    list_display = ("id", "username", "first_name", "email", "is_legacy", "activated_at")
    list_filter = ("is_legacy",)
    search_fields = ("username", "id", "email")


@admin.register(LegacyMigrationRecord)
class LegacyMigrationRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user", "balance", "device_count", "devices_trimmed", "remaining_paid_days",
        "days_from_balance", "days_granted", "capped", "floored", "unlimited",
    )
    list_filter = ("capped", "floored", "unlimited", "plan_device_limit")
    search_fields = ("user__username", "user__id")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("uuid", "user", "kind", "plan", "amount", "created_at", "processed_at")
    list_filter = ("kind",)


admin.site.register(UserInvitation)
admin.site.register(PromoCode)
admin.site.register(UsedPromoCode)
admin.site.register(AllowedUserPromoCode)
admin.site.register(Transaction)
