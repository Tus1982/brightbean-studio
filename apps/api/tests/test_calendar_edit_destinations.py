"""PATCH /posts/{id}: surface and destinations of the SAME post. [Willy 2026-10-17]

Willy's Studio edits a calendar exit in place. What these tests hold still:

* reads expose ``editable_fields`` and the per-platform override texts, and do not write;
* a surface / account change keeps the post id and the kept children untouched
  (id, ``platform_extra`` options, schedule, override texts);
* foreign or non-allowlisted accounts can be neither added nor removed;
* a post with any sent child is read-only for these fields;
* invalid media, a missing board or a bad surface means zero mutations;
* a scheduled post needs ``publish_directly``, the approval gate and the future time;
* ``apply_text_to_platforms`` clears only the overrides of the texts that were sent.
"""

import datetime as dt
import json
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="calendar-edit@example.com", password="testpass123", name="Calendar", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Calendar Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Calendar Workspace", organization=organization)


@pytest.fixture
def owner(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


def _account(workspace, platform, status="connected"):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=f"{platform}-{uuid.uuid4().hex[:6]}",
        account_name=f"Test {platform}",
        connection_status=status,
    )


def _asset(organization, workspace, media_type="image", width=1080, height=1350):
    from apps.media_library.models import MediaAsset

    return MediaAsset.objects.create(
        organization=organization,
        workspace=workspace,
        file=f"media_library/test/{uuid.uuid4().hex}.bin",
        filename="asset",
        media_type=media_type,
        width=width,
        height=height,
    )


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def client_for(db, user, owner, workspace):
    def _make(*accounts, permissions=None):
        key = services.issue_api_key(
            workspace=workspace,
            social_accounts=list(accounts),
            issued_by=user,
            name="calendar-edit",
            permissions=list(permissions if permissions is not None else PERMISSION_KEYS),
        )
        return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")

    return _make


@pytest.fixture
def ig(workspace):
    return _account(workspace, "instagram")


@pytest.fixture
def fb(workspace):
    return _account(workspace, "facebook")


@pytest.fixture
def tiktok(workspace):
    return _account(workspace, "tiktok")


def _post(workspace, accounts, media=(), status="draft", scheduled_at=None, extra=None):
    post = Post.objects.create(workspace=workspace, caption="Comune", title="Titolo", first_comment="")
    for account in accounts:
        PlatformPost.objects.create(
            post=post,
            social_account=account,
            status=status,
            scheduled_at=scheduled_at,
            platform_specific_caption="Solo qui",
            platform_extra=dict(extra or {"collaborators": ["willy"]}),
        )
    for position, asset in enumerate(media):
        PostMedia.objects.create(post=post, media_asset=asset, position=position)
    return post


def _patch(client, post, **body):
    return client.patch(f"/api/v1/posts/{post.id}", data=json.dumps(body, default=str), content_type="application/json")


def _snapshot(post):
    return sorted(
        (
            str(pp.id),
            str(pp.social_account_id),
            pp.status,
            json.dumps(pp.platform_extra, sort_keys=True),
            pp.platform_specific_caption,
        )
        for pp in PlatformPost.objects.filter(post=post)
    ) + [Post.objects.filter(id=post.id).values_list("caption", "title", "updated_at").get()]


@pytest.mark.django_db
class TestCapabilities:
    def test_read_exposes_editable_fields_and_override_texts(self, client_for, workspace, ig):
        post = _post(workspace, [ig])
        before = _snapshot(post)
        body = client_for(ig).get(f"/api/v1/posts/{post.id}").json()
        assert {"post_type", "social_account_ids", "apply_text_to_platforms", "caption"} <= set(body["editable_fields"])
        child = body["platform_posts"][0]
        assert child["platform_specific_caption"] == "Solo qui" and child["platform_specific_title"] is None
        assert child["platform_extra"] == {"collaborators": ["willy"]}
        assert _snapshot(post) == before, "a read never writes"

    def test_sent_post_has_no_editable_fields(self, client_for, workspace, ig):
        post = _post(workspace, [ig], status="published")
        assert client_for(ig).get(f"/api/v1/posts/{post.id}").json()["editable_fields"] == []


@pytest.mark.django_db
class TestSamePostDestinations:
    def test_add_account_keeps_id_and_kept_child(self, client_for, organization, workspace, ig, fb):
        post = _post(workspace, [ig], media=[_asset(organization, workspace)])
        kept = PlatformPost.objects.get(post=post)
        r = _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id), str(fb.id)])
        assert r.status_code == 200, r.content
        assert r.json()["id"] == str(post.id) and Post.objects.count() == 1
        again = PlatformPost.objects.get(id=kept.id)
        assert again.platform_extra == {"collaborators": ["willy"]} and again.platform_specific_caption == "Solo qui"
        new = PlatformPost.objects.get(post=post, social_account=fb)
        assert new.status == "draft" and new.platform_extra == {}

    def test_remove_account_deletes_only_that_draft(self, client_for, workspace, ig, fb):
        post = _post(workspace, [ig, fb])
        kept = PlatformPost.objects.get(post=post, social_account=ig)
        r = _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id)])
        assert r.status_code == 200, r.content
        assert list(PlatformPost.objects.filter(post=post).values_list("id", flat=True)) == [kept.id]

    def test_change_to_story_sets_hint_and_keeps_options(self, client_for, organization, workspace, ig):
        post = _post(workspace, [ig], media=[_asset(organization, workspace, width=1080, height=1920)])
        r = _patch(client_for(ig), post, post_type="story")
        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get(post=post).platform_extra == {"collaborators": ["willy"], "post_type": "story"}
        r = _patch(client_for(ig), post, post_type="post")
        assert PlatformPost.objects.get(post=post).platform_extra == {"collaborators": ["willy"]}

    def test_old_client_patch_is_unchanged(self, client_for, workspace, ig):
        post = _post(workspace, [ig])
        r = _patch(client_for(ig), post, caption="Nuovo")
        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get(post=post).platform_specific_caption == "Solo qui"


@pytest.mark.django_db
class TestRefusedWithoutWrites:
    @pytest.mark.parametrize(
        "body",
        [
            {"post_type": "story"},  # 4:5 image is not vertical
            {"post_type": "reel"},  # an image is not a video
            {"post_type": "carousel"},
            {"social_account_ids": []},
            {"social_account_ids": ["IG", "IG"]},
        ],
    )
    def test_invalid_surface_or_accounts(self, client_for, organization, workspace, ig, body):
        post = _post(workspace, [ig], media=[_asset(organization, workspace)])
        if "social_account_ids" in body:
            body = {"social_account_ids": [str(ig.id) for _ in body["social_account_ids"]]}
        before = _snapshot(post)
        r = _patch(client_for(ig), post, caption="Non deve passare", **body)
        assert r.status_code == 422, r.content
        assert _snapshot(post) == before

    def test_tiktok_needs_real_video(self, client_for, organization, workspace, ig, tiktok):
        post = _post(workspace, [ig], media=[_asset(organization, workspace)])
        before = _snapshot(post)
        assert _patch(client_for(ig, tiktok), post, social_account_ids=[str(ig.id), str(tiktok.id)]).status_code == 422
        assert _snapshot(post) == before

    def test_new_pinterest_without_board_is_refused(self, client_for, organization, workspace, ig):
        pin = _account(workspace, "pinterest")
        post = _post(workspace, [ig], media=[_asset(organization, workspace)])
        r = _patch(client_for(ig, pin), post, social_account_ids=[str(ig.id), str(pin.id)])
        assert r.status_code == 422 and b"board" in r.content
        assert PlatformPost.objects.filter(post=post).count() == 1

    def test_disconnected_account_is_refused(self, client_for, organization, workspace, ig):
        off = _account(workspace, "facebook", status="disconnected")
        post = _post(workspace, [ig], media=[_asset(organization, workspace)])
        assert _patch(client_for(ig, off), post, social_account_ids=[str(ig.id), str(off.id)]).status_code == 422

    def test_not_allowlisted_account_cannot_be_added(self, client_for, workspace, ig, fb):
        post = _post(workspace, [ig])
        assert _patch(client_for(ig), post, social_account_ids=[str(ig.id), str(fb.id)]).status_code == 403
        assert PlatformPost.objects.filter(post=post).count() == 1

    def test_foreign_child_cannot_be_removed(self, client_for, workspace, ig, fb):
        post = _post(workspace, [ig, fb])
        r = _patch(client_for(ig), post, social_account_ids=[str(ig.id)])
        assert r.status_code == 404, "a key without fb never sees, let alone removes, the fb child"
        assert PlatformPost.objects.filter(post=post).count() == 2

    @pytest.mark.parametrize("sent", ["published", "publishing", "awaiting_creator"])
    def test_partly_sent_post_is_read_only(self, client_for, workspace, ig, fb, sent):
        post = _post(workspace, [ig, fb])
        PlatformPost.objects.filter(post=post, social_account=fb).update(status=sent)
        before = _snapshot(post)
        r = _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id)])
        assert r.status_code == 409, r.content
        assert _snapshot(post) == before

    def test_mixed_states_are_refused(self, client_for, workspace, ig, fb):
        post = _post(workspace, [ig, fb])
        PlatformPost.objects.filter(post=post, social_account=fb).update(status="approved")
        assert _patch(client_for(ig, fb), post, post_type="post").status_code == 409


@pytest.mark.django_db
class TestScheduled:
    def _scheduled(self, workspace, accounts, organization, when=None):
        return _post(
            workspace,
            accounts,
            media=[_asset(organization, workspace)],
            status="scheduled",
            scheduled_at=when or timezone.now() + dt.timedelta(days=2),
        )

    def test_requires_publish_directly(self, client_for, organization, workspace, ig, fb):
        post = self._scheduled(workspace, [ig], organization)
        perms = [p for p in PERMISSION_KEYS if p != "publish_directly"]
        r = _patch(client_for(ig, fb, permissions=perms), post, social_account_ids=[str(ig.id), str(fb.id)])
        assert r.status_code == 403
        assert PlatformPost.objects.filter(post=post).count() == 1

    def test_approval_gate_blocks(self, client_for, organization, workspace, ig, fb):
        workspace.approval_workflow_mode = "required_internal"
        workspace.save()
        post = self._scheduled(workspace, [ig], organization)
        r = _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id), str(fb.id)])
        assert r.status_code == 422, r.content
        assert PlatformPost.objects.filter(post=post).count() == 1

    def test_new_child_joins_the_same_schedule(self, client_for, organization, workspace, ig, fb):
        when = timezone.now() + dt.timedelta(days=3)
        post = self._scheduled(workspace, [ig], organization, when=when)
        r = _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id), str(fb.id)])
        assert r.status_code == 200, r.content
        new = PlatformPost.objects.get(post=post, social_account=fb)
        assert new.status == "scheduled" and abs(new.scheduled_at - when) < dt.timedelta(seconds=1)

    def test_past_schedule_is_refused(self, client_for, organization, workspace, ig, fb):
        post = self._scheduled(workspace, [ig], organization, when=timezone.now() - dt.timedelta(minutes=5))
        assert _patch(client_for(ig, fb), post, social_account_ids=[str(ig.id), str(fb.id)]).status_code == 422


@pytest.mark.django_db
class TestTextOverrides:
    def test_apply_to_platforms_clears_only_sent_text(self, client_for, workspace, ig):
        post = _post(workspace, [ig])
        PlatformPost.objects.filter(post=post).update(platform_specific_title="T solo qui")
        r = _patch(client_for(ig), post, caption="Per tutti", apply_text_to_platforms=True)
        assert r.status_code == 200, r.content
        pp = PlatformPost.objects.get(post=post)
        assert pp.platform_specific_caption is None and pp.platform_specific_title == "T solo qui"
        assert pp.platform_extra == {"collaborators": ["willy"]}
        assert Post.objects.get(id=post.id).caption == "Per tutti"

    def test_default_keeps_overrides(self, client_for, workspace, ig):
        post = _post(workspace, [ig])
        _patch(client_for(ig), post, caption="Comune nuovo")
        assert PlatformPost.objects.get(post=post).platform_specific_caption == "Solo qui"

    def test_apply_to_platforms_refused_on_sent_post(self, client_for, workspace, ig):
        post = _post(workspace, [ig], status="scheduled", scheduled_at=timezone.now() + dt.timedelta(days=1))
        PlatformPost.objects.filter(post=post).update(published_at=timezone.now())
        r = _patch(client_for(ig), post, caption="X", apply_text_to_platforms=True)
        assert r.status_code == 409
        assert PlatformPost.objects.get(post=post).platform_specific_caption == "Solo qui"


@pytest.mark.django_db
def test_atomic_rollback_on_late_failure(client_for, organization, workspace, ig, fb, monkeypatch):
    """A failure after the first child write leaves nothing behind."""
    post = _post(workspace, [ig], media=[_asset(organization, workspace)])
    before = _snapshot(post)
    real_create = PlatformPost.objects.create

    def boom(**kwargs):
        real_create(**kwargs)
        raise RuntimeError("db lost mid-way")

    monkeypatch.setattr(PlatformPost.objects, "create", boom)
    client = client_for(ig, fb)
    client.raise_request_exception = False
    r = _patch(client, post, social_account_ids=[str(ig.id), str(fb.id)], caption="Mai salvato")
    assert r.status_code == 500
    assert _snapshot(post) == before


@pytest.mark.django_db
def test_new_foreign_child_between_lookup_and_lock_cannot_be_removed(client_for, workspace, ig, fb, monkeypatch):
    from apps.api.routers import posts as router

    post = _post(workspace, [ig])
    original = router._get_workspace_post

    def race(request, ident):
        value = original(request, ident)
        PlatformPost.objects.create(post=post, social_account=fb, status="draft")
        return value

    monkeypatch.setattr(router, "_get_workspace_post", race)
    result = _patch(client_for(ig), post, social_account_ids=[str(ig.id)])
    assert result.status_code == 404
    assert PlatformPost.objects.filter(post=post, social_account=fb).exists()


@pytest.mark.django_db
def test_post_becoming_in_review_after_lookup_is_not_edited(client_for, workspace, ig, monkeypatch):
    from apps.api.routers import posts as router

    post = _post(workspace, [ig])
    original = router._get_workspace_post

    def race(request, ident):
        value = original(request, ident)
        PlatformPost.objects.filter(post=post).update(status="pending_review")
        return value

    monkeypatch.setattr(router, "_get_workspace_post", race)
    result = _patch(client_for(ig), post, caption="Must stay unchanged", apply_text_to_platforms=True)
    assert result.status_code == 409
    post.refresh_from_db()
    assert post.caption == "Comune"
