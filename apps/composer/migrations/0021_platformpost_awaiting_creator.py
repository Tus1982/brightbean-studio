"""Add the ``awaiting_creator`` status for TikTok drafts uploads.

Choices-only change: the column is already a CharField(max_length=30), so this
adds no data and rewrites no rows. It exists so ``makemigrations --check`` stays
clean and so the admin/forms offer the new value.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0020_platformpost_first_comment_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformpost",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending_review", "Pending Review"),
                    ("pending_client", "Pending Client"),
                    ("approved", "Approved"),
                    ("changes_requested", "Changes Requested"),
                    ("rejected", "Rejected"),
                    ("scheduled", "Scheduled"),
                    ("publishing", "Publishing"),
                    ("awaiting_creator", "In TikTok drafts"),
                    ("published", "Published"),
                    ("failed", "Failed"),
                    ("on_hold", "On Hold"),
                ],
                db_index=True,
                default="draft",
                max_length=30,
            ),
        ),
    ]
