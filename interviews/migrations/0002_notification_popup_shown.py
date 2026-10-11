from django.db import migrations, models


def mark_existing_as_shown(apps, schema_editor):
    # Notifications that predate live pop-ups must not all pop at once.
    apps.get_model("interviews", "Notification").objects.update(popup_shown=True)


class Migration(migrations.Migration):

    dependencies = [
        ('interviews', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='popup_shown',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_as_shown, migrations.RunPython.noop),
    ]
