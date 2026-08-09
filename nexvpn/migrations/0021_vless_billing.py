# Шаг 1/3 миграции на VLESS: только добавления.
# Легаси-таблицы WireGuard ещё на месте — из них читает дата-миграция 0022.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexvpn', '0020_nexuser_first_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_limit', models.PositiveSmallIntegerField(unique=True)),
                ('price_month', models.PositiveIntegerField(help_text='Цена за 30 дней, ₽')),
                ('name', models.CharField(max_length=63)),
                ('is_active', models.BooleanField(default=True, help_text='Можно купить или перейти')),
                ('is_public', models.BooleanField(default=True, help_text='Показывать в списке тарифов')),
                ('order', models.IntegerField(default=0)),
            ],
            options={
                'ordering': ['order', 'device_limit'],
            },
        ),
        migrations.AddField(
            model_name='nexuser',
            name='activated_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='nexuser',
            name='is_legacy',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='payment',
            name='amount',
            field=models.PositiveIntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='kind',
            field=models.CharField(blank=True, choices=[('purchase', 'Оплата периода подписки'), ('plan_change', 'Доплата за смену тарифа')], default=None, max_length=31, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='processed_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='user',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.CASCADE, to='nexvpn.nexuser'),
        ),
        migrations.AddField(
            model_name='promocode',
            name='bonus_days',
            field=models.PositiveIntegerField(default=0, help_text='Сколько дней подписки даёт'),
        ),
        migrations.AddField(
            model_name='userinvitation',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='userinvitation',
            name='inviter_notified_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='userinvitation',
            name='reward_granted_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AlterField(
            model_name='promocode',
            name='value',
            field=models.IntegerField(blank=True, default=None, help_text='Легаси: номинал в ₽', null=True),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='type',
            field=models.CharField(choices=[('purchase_subscription', 'Оплата подписки'), ('plan_upgrade', 'Доплата за переход на больший тариф'), ('renew_subscription', 'Продление подписки'), ('reactivate_clint', 'Возобновление подписки'), ('invitation', 'Приглашение пользователя'), ('start_balance', 'Стартовый баланс'), ('fill_up_balance', 'Пополнение баланса'), ('add_device', 'Добавление устройства'), ('promo_code', 'Применение промокода'), ('updated_by_admin', 'Изменено администратором')], max_length=31),
        ),
        migrations.AlterField(
            model_name='userinvitation',
            name='inviter',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_invitations', to='nexvpn.nexuser'),
        ),
        migrations.CreateModel(
            name='LegacyMigrationRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('balance', models.IntegerField(help_text='Баланс на момент миграции, ₽')),
                ('device_count', models.PositiveIntegerField(help_text='Активных устройств на дату среза')),
                ('devices_trimmed', models.PositiveIntegerField(default=0, help_text='Сколько устройств срезано сверх лимита 10')),
                ('remaining_paid_days', models.PositiveIntegerField(default=0, help_text='Остаток оплаченного периода, дн.')),
                ('days_from_balance', models.PositiveIntegerField(default=0)),
                ('days_granted', models.PositiveIntegerField(default=0, help_text='Итого начислено, после потолка и порога')),
                ('capped', models.BooleanField(default=False, help_text='Срезано потолком LEGACY_MAX_DAYS')),
                ('floored', models.BooleanField(default=False, help_text='Поднято нижним порогом LEGACY_MIN_DAYS')),
                ('unlimited', models.BooleanField(default=False, help_text='Баланс был залит вручную → безлимит')),
                ('plan_device_limit', models.PositiveSmallIntegerField()),
                ('cutoff_date', models.DateField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='legacy_record', to='nexvpn.nexuser')),
            ],
        ),
        migrations.AddField(
            model_name='payment',
            name='plan',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.PROTECT, to='nexvpn.plan'),
        ),
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('panel_user_id', models.PositiveIntegerField(blank=True, default=None, null=True)),
                ('panel_short_uuid', models.CharField(blank=True, default=None, max_length=63, null=True)),
                ('subscription_url', models.URLField(blank=True, default=None, max_length=255, null=True)),
                ('panel_status', models.CharField(choices=[('never_synced', 'Ни разу не синхронизирована'), ('synced', 'Синхронизирована'), ('pending', 'Ожидает синхронизации'), ('failed', 'Ошибка синхронизации')], default='never_synced', max_length=31)),
                ('panel_synced_at', models.DateTimeField(blank=True, default=None, null=True)),
                ('panel_error', models.TextField(blank=True, default='')),
                ('payment_method_id', models.CharField(blank=True, default=None, max_length=63, null=True)),
                ('auto_renew_agreed', models.BooleanField(default=False)),
                ('next_plan', models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='scheduled_subscriptions', to='nexvpn.plan')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='subscriptions', to='nexvpn.plan')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='subscription', to='nexvpn.nexuser')),
            ],
        ),
        migrations.CreateModel(
            name='SubscriptionEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(choices=[('trial', 'Пробный период нового пользователя'), ('legacy_migration', 'Перенос баланса из старой версии'), ('legacy_no_devices', 'Компенсация за отсутствие устройств'), ('purchase', 'Оплата подписки'), ('plan_upgrade', 'Переход на больший тариф'), ('plan_downgrade_scheduled', 'Запланирован переход на меньший тариф'), ('plan_downgrade_applied', 'Применён переход на меньший тариф'), ('referral_inviter', 'Бонус за приглашённого пользователя'), ('referral_invitee', 'Бонус за приход по реферальной ссылке'), ('promo_code', 'Применение промокода'), ('admin_adjustment', 'Изменено администратором'), ('expired', 'Подписка истекла')], max_length=31)),
                ('delta_days', models.IntegerField(default=0)),
                ('price_month', models.PositiveIntegerField(blank=True, default=None, null=True)),
                ('amount', models.PositiveIntegerField(default=0, help_text='Сколько заплачено, ₽')),
                ('expires_at_before', models.DateTimeField(blank=True, default=None, null=True)),
                ('expires_at_after', models.DateTimeField(blank=True, default=None, null=True)),
                ('comment', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('payment', models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, to='nexvpn.payment')),
                ('plan', models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.PROTECT, to='nexvpn.plan')),
                ('subscription', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events', to='nexvpn.subscription')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscription_events', to='nexvpn.nexuser')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
