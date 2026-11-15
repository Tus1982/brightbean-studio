"""Background tasks for the publishing engine."""

import logging

from background_task import background

logger = logging.getLogger(__name__)

# Both tasks are registered on these intervals by apps.publisher.apps.
PUBLISH_CYCLE_INTERVAL_SECONDS = 15
# The confirmation sweep only touches rows in ``publishing``, which is a handful
# at any moment, so a minute is frequent enough to feel immediate without
# polling TikTok harder than it wants.
PUBLISH_CONFIRM_INTERVAL_SECONDS = 60
# [fork Willy] TikTok inbox drafts are settled by a person in the TikTok app,
# not by a machine: ten minutes is plenty (``reconcile_awaiting_creator``).
RECONCILE_AWAITING_CREATOR_INTERVAL_SECONDS = 600


@background(schedule=0)
def reconcile_awaiting_creator():
    """Move TikTok inbox uploads on once the creator acts (or doesn't).

    A row in ``awaiting_creator`` is a video sitting in someone's TikTok
    drafts. TikTok's ``/v2/post/publish/status/fetch/`` endpoint is the only
    thing that knows what happened to it:

    * ``PUBLISH_COMPLETE`` -> the creator posted it, so it is published now;
    * ``FAILED`` / ``EXPIRED`` -> it will never go out;
    * anything else -> still waiting, leave it alone.

    Never republishes: the video is already on TikTok's side, so a retry would
    upload it twice and burn one of the five pending-upload slots TikTok allows
    per day.
    """
    from apps.publisher.engine import PublishEngine

    moved = PublishEngine().reconcile_awaiting_creator()
    if moved:
        logger.info("Reconciled %d TikTok inbox upload(s)", moved)


@background(schedule=0)
def run_publish_cycle():
    """Poll for due posts and publish them.

    Registered as a recurring task (every 15s) so that
    ``python manage.py run_studio_worker`` handles publishing
    without needing a separate ``run_publisher`` process.
    """
    from apps.publisher.engine import PublishEngine

    engine = PublishEngine()
    published = engine.poll_and_publish()
    if published:
        logger.info("Publish cycle completed - %d post(s) published", published)


@background(schedule=0)
def confirm_pending_publishes():
    """Settle posts left in ``publishing``.

    Confirms asynchronous publishes against the platform (TikTok transcodes
    after accepting the upload) and fails rows whose worker died mid-publish —
    the only thing that ever gets those out of a status the UI shows as
    read-only. See ``PublishEngine.confirm_pending_publishes``.
    """
    from apps.publisher.engine import PublishEngine

    settled = PublishEngine().confirm_pending_publishes()
    if settled:
        logger.info("Publish confirmation sweep settled %d platform post(s)", settled)
