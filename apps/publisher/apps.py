import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PublisherConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.publisher"
    verbose_name = "Publishing Engine"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._register_publish_task, sender=self)

    @staticmethod
    def _register_publish_task(sender, **kwargs):
        """Register the recurring publish-cycle task after migrations are applied."""
        try:
            from background_task.models import Task

            from apps.publisher.tasks import reconcile_awaiting_creator, run_publish_cycle

            if not Task.objects.filter(verbose_name="run_publish_cycle").exists():
                run_publish_cycle(
                    repeat=15,
                    verbose_name="run_publish_cycle",
                )
                logger.info("Registered recurring publish task (every 15s)")

            # Ten minutes is plenty: it waits on a person opening the TikTok
            # app, not on a machine.
            if not Task.objects.filter(verbose_name="reconcile_awaiting_creator").exists():
                reconcile_awaiting_creator(
                    repeat=600,
                    verbose_name="reconcile_awaiting_creator",
                )
                logger.info("Registered recurring TikTok inbox reconciliation (every 10m)")
        except Exception:
            logger.debug("Skipping publish task registration (database not ready)")
