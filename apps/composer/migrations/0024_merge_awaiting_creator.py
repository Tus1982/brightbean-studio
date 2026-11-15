"""Merge the fork's ``awaiting_creator`` branch into upstream's 0021-0023.

The fork added ``0021_platformpost_awaiting_creator`` on top of 0020 before
upstream shipped its own 0021-0023. The fork's file keeps its original name on
purpose: deployments that already applied it have that exact name recorded in
``django_migrations``, so renaming it would make Django try to apply it again.
This merge node joins the two leaves; it runs no operations.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0021_platformpost_awaiting_creator"),
        ("composer", "0023_platformpost_youtube_video_checked_at"),
    ]

    operations = []
