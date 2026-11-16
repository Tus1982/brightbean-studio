"""Exceptional archives retain rows; media use canonical workspace checks; reads are honest."""

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.api.tests import test_calendar_edit_destinations as shared
from apps.api.tests.test_willy_draft_operations import call, make_post
from apps.composer.models import PostMedia

user = shared.user
organization = shared.organization
workspace = shared.workspace
owner = shared.owner
ig = shared.ig
client_for = shared.client_for
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "status,op", [("failed", "archive_error"), ("approved", "archive_test"), ("awaiting_creator", "archive_test")]
)
def test_exceptional_archive_retains_status_and_row(workspace, ig, client_for, status, op):
    post, child = make_post(workspace, ig, status)
    if status == "failed":
        from apps.publisher.models import PublishLog

        PublishLog.objects.create(platform_post=child, status_code=400)
    client = client_for(ig)
    assert call(client, post, op, motivo="Test esplicito", dry_run=True).status_code == 200
    post.refresh_from_db()
    assert not post.internal_notes
    response = call(client, post, op, motivo="Test esplicito")
    assert response.status_code == 200, response.content
    post.refresh_from_db()
    child.refresh_from_db()
    assert '"column": "ARCHIVE"' in post.internal_notes and child.status == status


@pytest.mark.parametrize("status", ["scheduled", "publishing", "published"])
@pytest.mark.parametrize("op", ["archive_error", "archive_test"])
def test_exceptional_archive_always_refuses_live_statuses(workspace, ig, client_for, status, op):
    post, child = make_post(workspace, ig, status)
    assert call(client_for(ig), post, op, motivo="Test").status_code == 409
    post.refresh_from_db()
    assert not post.internal_notes


def test_media_replacement_uses_existing_workspace_validation(workspace, ig, client_for, organization):
    post, _ = make_post(workspace, ig)
    asset = shared._asset(organization, workspace)
    response = call(client_for(ig), post, "edit", changes={"media_asset_ids": [str(asset.id)]})
    assert response.status_code == 200, response.content
    assert list(PostMedia.objects.filter(post=post).values_list("media_asset_id", flat=True)) == [asset.id]


def test_missing_snapshot_not_zero_and_captured_zero_available(workspace, ig):
    from apps.analytics.api_builders import build_account_analytics
    from apps.analytics.models import AccountInsightsSnapshot

    empty = build_account_analytics(ig, 30)
    assert not empty.analytics_available and empty.captured_at is None and not empty.hero_metrics
    AccountInsightsSnapshot.objects.create(social_account=ig, metric_key="reach", date=timezone.now().date(), value=0)
    measured = build_account_analytics(ig, 30)
    assert measured.analytics_available and measured.captured_at


def test_reviews_scoped_to_allowlist_and_read_permission(workspace, ig, client_for):
    google = shared._account(workspace, "google_business")
    with patch("apps.analytics.tasks._resolve_provider") as resolve:
        assert client_for(ig).get(f"/api/v1/accounts/{google.id}/google/reviews").status_code == 403
        assert (
            client_for(google, permissions=["create_posts"])
            .get(f"/api/v1/accounts/{google.id}/google/reviews")
            .status_code
            == 403
        )
        resolve.assert_not_called()


def test_review_failure_is_explicit_and_bounded_success(workspace, client_for):
    google = shared._account(workspace, "google_business")
    client = client_for(google)
    with patch("apps.analytics.tasks._resolve_provider") as resolve:
        resolve.return_value.get_reviews.side_effect = RuntimeError("forbidden")
        assert client.get(f"/api/v1/accounts/{google.id}/google/reviews").status_code == 502
        resolve.return_value.get_reviews.side_effect = None
        resolve.return_value.get_reviews.return_value = {"average_rating": 4.6, "reviews": [], "troncato": False}
        r = client.get(f"/api/v1/accounts/{google.id}/google/reviews?limite=1")
        assert r.status_code == 200 and r.json()["average_rating"] == 4.6
