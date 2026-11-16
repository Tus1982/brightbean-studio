"""Le scritture Willy si fermano alle bozze: transazione, audit, zero publisher."""

import json
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.api.tests import test_calendar_edit_destinations as calendar_fixtures
from apps.api_keys.models import ApiKeyAuditLog
from apps.composer.models import PlatformPost, Post
from apps.publisher.models import PublishLog

# Bind the shared fixtures in this module too: pytest may already have collected
# their source module before this test file during the full suite.
user = calendar_fixtures.user
organization = calendar_fixtures.organization
workspace = calendar_fixtures.workspace
owner = calendar_fixtures.owner
ig = calendar_fixtures.ig
client_for = calendar_fixtures.client_for
pytestmark = pytest.mark.django_db


def make_post(workspace, ig, status="draft"):
    post = Post.objects.create(workspace=workspace, title="Prova", caption="Prima")
    child = PlatformPost.objects.create(post=post, social_account=ig, status=status)
    return post, child


def call(client, post, operation, **kwargs):
    return client.post(
        f"/api/v1/posts/{post.id}/draft-operation",
        data=json.dumps({"operation": operation, "richiesto_da": "jacopo", **kwargs}),
        content_type="application/json",
    )


@pytest.mark.parametrize("operation", ["archive", "delete"])
def test_dry_run_changes_nothing(workspace, ig, client_for, operation):
    post, child = make_post(workspace, ig)
    response = call(client_for(ig), post, operation, motivo="Prova", dry_run=True)
    assert response.status_code == 200
    assert response.json()["kanban"] == ("ARCHIVE" if operation == "archive" else "DELETED")
    post.refresh_from_db()
    child.refresh_from_db()
    assert post.internal_notes == "" and child.status == "draft"
    assert not ApiKeyAuditLog.objects.filter(action="post.draft." + operation).exists()


@pytest.mark.parametrize(
    "status", ["scheduled", "published", "publishing", "awaiting_creator", "approved", "pending_review"]
)
@pytest.mark.parametrize("operation", ["archive", "delete", "edit", "move"])
def test_protected_states_refused(workspace, ig, client_for, status, operation):
    post, child = make_post(workspace, ig, status)
    options = {"motivo": "Prova"}
    if operation == "edit":
        options["changes"] = {"caption": "Dopo"}
    if operation == "move":
        options["column"] = "TO_APPROVE"
    response = call(client_for(ig), post, operation, **options)
    assert response.status_code == 409
    post.refresh_from_db()
    child.refresh_from_db()
    assert post.caption == "Prima" and post.internal_notes == "" and child.status == status


def test_partial_publication_and_attempt_history_block_delete(workspace, ig, client_for):
    post, child = make_post(workspace, ig)
    PublishLog.objects.create(platform_post=child, status_code=200)
    assert call(client_for(ig), post, "delete", motivo="Test").status_code == 409
    child.published_at = timezone.now()
    child.save()
    assert call(client_for(ig), post, "archive", motivo="Test").status_code == 409
    assert Post.objects.filter(pk=post.pk).exists()


def test_archive_retains_post_and_audits_actor(workspace, ig, client_for):
    post, child = make_post(workspace, ig)
    response = call(client_for(ig), post, "archive", motivo="Duplicato")
    assert response.status_code == 200
    post.refresh_from_db()
    assert '"column": "ARCHIVE"' in post.internal_notes
    log = ApiKeyAuditLog.objects.get(action="post.draft.archive")
    assert log.target_id == post.pk and log.created_at and json.loads(log.user_agent)["richiesto_da"] == "jacopo"
    child.refresh_from_db()
    assert child.status == "draft" and child.scheduled_at is None


def test_delete_removes_only_draft_and_keeps_audit(workspace, ig, client_for):
    post, child = make_post(workspace, ig)
    pid = post.pk
    assert call(client_for(ig), post, "delete", motivo="Test da eliminare").status_code == 200
    assert not Post.objects.filter(pk=pid).exists()
    assert ApiKeyAuditLog.objects.filter(target_id=pid, action="post.draft.delete").exists()


@pytest.mark.parametrize("column", ["APPROVED", "SCHEDULED", "PUBLISHED", "ERROR"])
def test_no_escalation(workspace, ig, client_for, column):
    post, _ = make_post(workspace, ig)
    assert call(client_for(ig), post, "move", column=column).status_code == 422


def test_edit_reuses_validation_and_preserves_approval_marker(workspace, ig, client_for):
    post, child = make_post(workspace, ig)
    client = client_for(ig)
    assert call(client, post, "move", column="TO_APPROVE").status_code == 200
    response = call(client, post, "edit", changes={"caption": "Dopo", "internal_notes": "Nota nuova"})
    assert response.status_code == 200
    post.refresh_from_db()
    child.refresh_from_db()
    assert post.caption == "Dopo" and "Nota nuova" in post.internal_notes
    assert '"column": "TO_APPROVE"' in post.internal_notes
    assert '"human_approval_required": true' in post.internal_notes
    assert child.status == "draft" and child.scheduled_at is None


@pytest.mark.parametrize("changes", [{"scheduled_at": "2027-01-01T12:00:00Z"}, {"post_type": "story"}])
def test_schedule_fields_refused(workspace, ig, client_for, changes):
    post, _ = make_post(workspace, ig)
    assert call(client_for(ig), post, "edit", changes=changes).status_code == 422


def test_audit_failure_rolls_back(workspace, ig, client_for):
    post, _ = make_post(workspace, ig)
    client = client_for(ig)
    with (
        patch.object(ApiKeyAuditLog.objects, "create", side_effect=RuntimeError("audit offline")),
        pytest.raises(RuntimeError, match="audit offline"),
    ):
        call(client, post, "delete", motivo="Prova")
    assert Post.objects.filter(pk=post.pk).exists()


def test_permissions_and_foreign_children_are_checked(workspace, ig, client_for):
    post, _ = make_post(workspace, ig)
    assert call(client_for(ig, permissions=[]), post, "archive", motivo="Test").status_code == 403
    foreign = calendar_fixtures._account(workspace, "facebook")
    PlatformPost.objects.create(post=post, social_account=foreign, status="draft")
    assert call(client_for(ig), post, "delete", motivo="Test").status_code == 404
    assert Post.objects.filter(pk=post.pk).exists()


def test_state_changed_between_lookup_and_lock_is_refused(workspace, ig, client_for, monkeypatch):
    from apps.api.routers import posts

    post, child = make_post(workspace, ig)
    original = posts._get_workspace_post
    calls = []

    def lookup(*args):
        result = original(*args)
        if not calls:
            PlatformPost.objects.filter(pk=child.pk).update(status="scheduled")
        calls.append(True)
        return result

    monkeypatch.setattr(posts, "_get_workspace_post", lookup)
    assert call(client_for(ig), post, "edit", changes={"caption": "Dopo"}).status_code == 409
    post.refresh_from_db()
    assert post.caption == "Prima"
