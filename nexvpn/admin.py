from django.contrib import admin, messages
from django.shortcuts import render

from nexvpn.enums import BroadcastStatusEnum
from nexvpn.models import (
    AllowedUserPromoCode,
    BillingPeriod,
    Broadcast,
    BroadcastDelivery,
    GlobalSettings,
    LegacyMigrationRecord,
    NexUser,
    NodeUsageDay,
    PanelPresence,
    Payment,
    Plan,
    PromoCode,
    Subscription,
    SubscriptionEvent,
    Transaction,
    UsageDashboard,
    UsedPromoCode,
    UserInvitation,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Цены правятся здесь. Уже начисленные дни при этом не пересчитываются:
    в SubscriptionEvent лежит снимок цены на момент операции."""

    list_display = ("name", "device_limit", "price_month", "is_active", "is_public", "order")
    list_editable = ("price_month", "is_active", "is_public", "order")


@admin.register(BillingPeriod)
class BillingPeriodAdmin(admin.ModelAdmin):
    """Цены не хранятся: они считаются из цены тарифа и этой скидки."""

    list_display = ("months", "discount_percent", "is_active", "order", "example_prices")
    list_editable = ("discount_percent", "is_active", "order")

    @admin.display(description="Пример цены (1 / 3 / 10 устр.)")
    def example_prices(self, obj):
        from nexvpn.models import Plan

        parts = []
        for limit in (1, 3, 10):
            plan = Plan.objects.filter(device_limit=limit).first()
            if plan:
                parts.append(f"{obj.price_for(plan)}₽")
        return " · ".join(parts) or "—"


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    """Синглтон: строка одна, добавить или удалить её нельзя."""

    fieldsets = (
        ("Технические работы", {
            "fields": (
                "maintenance_mode", "maintenance_message",
                "maintenance_until", "maintenance_affects_admin",
            ),
            "description": "Включённый режим переводит бота на заглушку. Приём платежей "
                           "и синхронизацию с панелью не трогает: оплаченное доедет.",
        }),
        ("Рассылки от бота", {
            "fields": ("reminders_enabled",),
            "description": "Напоминания об окончании подписки. Выключение не трогает "
                           "сбор статистики и сверку платежей — они идут по тому же "
                           "расписанию, но от флага не зависят.",
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


def human_bytes(value: int | None) -> str:
    value = value or 0
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


@admin.register(UsageDashboard)
class UsageDashboardAdmin(admin.ModelAdmin):
    """Страница «кто чем пользуется». Ничего не хранит, только показывает."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from nexvpn import telemetry

        try:
            days = max(1, min(90, int(request.GET.get("days", 7))))
        except ValueError:
            days = 7

        nodes = telemetry.usage_by_node(days)
        users = telemetry.usage_by_user(days)
        silent = telemetry.silent_users()
        total = sum(row["bytes"] or 0 for row in nodes) or 1

        context = {
            **self.admin_site.each_context(request),
            "title": "Использование туннелей",
            "days": days,
            "periods": (1, 7, 30),
            "nodes": [
                {
                    "name": row["node_name"] or "неизвестно",
                    "users": row["users"],
                    "bytes": human_bytes(row["bytes"]),
                    "share": round(100 * (row["bytes"] or 0) / total),
                }
                for row in nodes
            ],
            "users": [
                {
                    "user": row["user"],
                    "bytes": human_bytes(row["bytes"]),
                    "nodes": ", ".join(
                        f"{name or 'неизвестно'} ({human_bytes(value)})" for name, value in row["nodes"]
                    ),
                    "online_at": row["online_at"],
                    "current_node": row["current_node"],
                }
                for row in row_limit(users)
            ],
            "silent": [
                {
                    "user": row["user"],
                    "never": row["never_connected"],
                    "last_online": row["last_online"],
                    "expires_at": row["expires_at"],
                }
                for row in silent
            ],
            "silent_total": len(silent),
        }
        return render(request, "nexvpn/usage_dashboard.html", context)


def row_limit(rows, limit: int = 50):
    return rows[:limit]


@admin.register(PanelPresence)
class PanelPresenceAdmin(admin.ModelAdmin):
    list_display = ("user", "node_name", "online_at", "used_traffic", "updated_at")
    list_filter = ("node_name",)
    search_fields = ("user__username", "user__pk")
    readonly_fields = tuple(field.name for field in PanelPresence._meta.fields)


@admin.register(NodeUsageDay)
class NodeUsageDayAdmin(admin.ModelAdmin):
    list_display = ("date", "node_name", "user", "bytes", "samples")
    list_filter = ("node_name", "date")
    search_fields = ("user__username", "user__pk")


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "status", "recipients_count", "sent_count", "failed_count", "created_at")
    list_filter = ("status", "audience")
    readonly_fields = ("status", "sent_count", "failed_count", "created_at", "started_at", "finished_at")
    actions = ("action_send_to_admin", "action_send")
    fieldsets = (
        (None, {"fields": ("title", "text")}),
        ("Кому и как", {"fields": ("audience", "with_connect_button", "with_menu_button", "test_only")}),
        ("Результат", {"fields": ("status", "sent_count", "failed_count",
                                  "created_at", "started_at", "finished_at")}),
    )

    @admin.display(description="Получателей сейчас")
    def recipients_count(self, obj):
        from bot.broadcast import recipients

        return len(recipients(obj))

    def _enqueue(self, request, queryset, *, test_only: bool):
        from bot.broadcast import recipients
        from nexvpn.tasks import send_broadcast

        for broadcast in queryset:
            if broadcast.status == BroadcastStatusEnum.SENDING:
                self.message_user(request, f"«{broadcast.title}» уже отправляется", messages.WARNING)
                continue
            # Флаг сохраняем в самой рассылке: задача читает её из базы, и
            # передавать режим отдельным аргументом значит рискнуть рассинхроном.
            Broadcast.objects.filter(pk=broadcast.pk).update(test_only=test_only)
            broadcast.refresh_from_db()
            count = len(recipients(broadcast))
            if not count:
                self.message_user(request, f"«{broadcast.title}»: некому отправлять", messages.WARNING)
                continue
            send_broadcast.delay(broadcast.pk)
            where = "только тебе" if test_only else f"{count} получателям"
            self.message_user(request, f"«{broadcast.title}» отправляется {where}", messages.SUCCESS)

    @admin.action(description="Отправить только мне (проверка)")
    def action_send_to_admin(self, request, queryset):
        self._enqueue(request, queryset, test_only=True)

    @admin.action(description="Отправить получателям")
    def action_send(self, request, queryset):
        self._enqueue(request, queryset, test_only=False)


@admin.register(BroadcastDelivery)
class BroadcastDeliveryAdmin(admin.ModelAdmin):
    list_display = ("broadcast", "user", "is_delivered", "error", "created_at")
    list_filter = ("is_delivered", "broadcast")
    search_fields = ("user__username", "user__pk", "error")
    readonly_fields = tuple(field.name for field in BroadcastDelivery._meta.fields)
