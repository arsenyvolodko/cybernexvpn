# Generated manually — тарифная логика бонуса инвайтеру, см. reference/../CLAUDE.md.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexvpn', '0044_globalsettings_referral_days'),
    ]

    operations = [
        migrations.RenameField(
            model_name='globalsettings',
            old_name='referral_inviter_days',
            new_name='referral_inviter_days_min',
        ),
        migrations.AlterField(
            model_name='globalsettings',
            name='referral_inviter_days_min',
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text=(
                    'Начисляется тому, кто позвал, если на момент первой оплаты '
                    'приглашённого тариф инвайтера дороже (₽/мес) того, что оплатил '
                    'приглашённый.'
                ),
                verbose_name='Дней инвайтеру — его тариф дороже',
            ),
        ),
        migrations.AddField(
            model_name='globalsettings',
            name='referral_inviter_days_max',
            field=models.PositiveSmallIntegerField(
                default=30,
                help_text=(
                    'Начисляется тому, кто позвал, если на момент первой оплаты '
                    'приглашённого тариф инвайтера не дороже (₽/мес) того, что '
                    'оплатил приглашённый — это же значение получает инвайтер, у '
                    'которого своей подписки ещё нет.'
                ),
                verbose_name='Дней инвайтеру — его тариф не дороже',
            ),
        ),
    ]
