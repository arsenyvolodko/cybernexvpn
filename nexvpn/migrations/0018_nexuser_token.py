import uuid

from django.db import migrations, models


def gen_tokens(apps, schema_editor):
    NexUser = apps.get_model("nexvpn", "NexUser")
    for user in NexUser.objects.filter(token=None):
        user.token = uuid.uuid4()
        user.save(update_fields=["token"])


class Migration(migrations.Migration):

    dependencies = [
        ("nexvpn", "0017_server_is_hidden"),
    ]

    operations = [
        migrations.AddField(
            model_name="nexuser",
            name="token",
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(gen_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="nexuser",
            name="token",
            field=models.UUIDField(default=uuid.uuid4, unique=True),
        ),
    ]