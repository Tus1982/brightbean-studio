"""``post_type`` on POST /posts/ — asking for a Story or a Reel. [2026-09-07]

The publish engine already reads a ``post_type`` hint out of
``platform_extra``, and both Meta providers can publish the surfaces. What was
missing was the way to *ask*: the REST API had no field, so every post created
by an agent was a feed post, and the vertical 1080x1920 file we render for every
delivery had nowhere to go.

What these tests hold still:

* the hint reaches ``platform_extra`` (that is the channel the engine reads);
* a surface the target platform cannot publish is refused at CREATE time, not
  three hours later at publish time when nobody is watching;
* only ``story``/``reel`` can be asked for — the feed types stay derived from
  the media, so a caller cannot contradict their own attachments;
* a story or reel without media is refused: both are made of the media.
"""

import json
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership

# A 1x1 PNG: enough for the media pipeline's magic-byte sniff.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="story-owner@example.com",
        password="testpass123",
        name="Story Owner",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Story Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Story Workspace", organization=organization)


@pytest.fixture
def owner_memberships(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )


def _account(workspace, platform, platform_id):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=platform_id,
        account_name=f"Test {platform}",
        connection_status="connected",
    )


@pytest.fixture
def instagram_account(db, workspace):
    return _account(workspace, "instagram", "ig-1")


@pytest.fixture
def facebook_account(db, workspace):
    return _account(workspace, "facebook", "fb-1")


@pytest.fixture
def linkedin_account(db, workspace):
    return _account(workspace, "linkedin_personal", "li-1")


@pytest.fixture
def image_asset(db, organization, workspace, user):
    from apps.media_library.services import create_asset

    return create_asset(
        organization=organization,
        workspace=workspace,
        uploaded_file=SimpleUploadedFile("story.png", PNG_1PX, content_type="image/png"),
        uploaded_by=user,
    )


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def client_for(db, user, owner_memberships, workspace):
    """A client whose key is scoped to exactly the accounts asked for."""

    def _make(*accounts):
        key = services.issue_api_key(
            workspace=workspace,
            social_accounts=list(accounts),
            issued_by=user,
            name="story-tests",
            permissions=list(PERMISSION_KEYS),
        )
        return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")

    return _make


def _create(client, account, **extra):
    body = {
        "social_account_id": str(account.id),
        "caption": "Una storia",
        "action": "draft",
    }
    body.update(extra)
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


@pytest.mark.django_db
class TestPostTypeStoryReel:
    def test_story_on_instagram_lands_in_platform_extra(self, client_for, instagram_account, image_asset):
        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="story", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get()
        assert pp.platform_extra == {"post_type": "story"}
        # e si rilegge dall'API, se no chi ha creato il post non sa cosa ha chiesto
        assert r.json()["platform_posts"][0]["platform_extra"]["post_type"] == "story"

    def test_story_on_facebook_is_accepted_too(self, client_for, facebook_account, image_asset):
        client = client_for(facebook_account)
        r = _create(client, facebook_account, post_type="story", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {"post_type": "story"}

    def test_a_normal_post_keeps_platform_extra_empty(self, client_for, instagram_account, image_asset):
        """Nessun tipo chiesto = feed, e il campo resta vuoto: il motore continua
        a dedurre dalle immagini come ha sempre fatto."""
        client = client_for(instagram_account)
        r = _create(client, instagram_account, media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {}

    def test_a_platform_without_stories_is_refused_at_creation(self, client_for, linkedin_account, image_asset):
        client = client_for(linkedin_account)
        r = _create(client, linkedin_account, post_type="story", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 422, r.content
        assert PlatformPost.objects.count() == 0

    def test_the_feed_types_cannot_be_forced(self, client_for, instagram_account, image_asset):
        """`carousel`/`image` non si chiedono: li decide il motore dalle immagini,
        e poterli scrivere qui vorrebbe dire contraddire gli allegati."""
        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="carousel", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 422, r.content

    def test_an_invented_type_is_refused(self, client_for, instagram_account, image_asset):
        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="fotoromanzo", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 422, r.content

    def test_a_story_without_media_is_refused(self, client_for, instagram_account):
        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="story")
        assert r.status_code == 422, r.content
        assert PlatformPost.objects.count() == 0

    def test_an_empty_post_type_is_the_same_as_not_asking(self, client_for, instagram_account, image_asset):
        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {}

    def test_the_hint_is_the_one_the_engine_reads(self, client_for, instagram_account, image_asset):
        """Il contratto vero: quello che l'API scrive è quello che il motore rilegge."""
        from apps.publisher.engine import PublishEngine
        from providers.types import PostType

        client = client_for(instagram_account)
        r = _create(client, instagram_account, post_type="story", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get()
        risolto = PublishEngine._resolve_post_type(
            platform="instagram",
            platform_extra=pp.platform_extra,
            media_count=1,
            first_media_type="image",
        )
        assert risolto == PostType.STORY

    def test_an_unknown_account_id_is_still_a_403(self, client_for, instagram_account):
        """Il controllo del tipo non deve scavalcare quello dell'allowlist."""
        client = client_for(instagram_account)
        r = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(uuid.uuid4()),
                    "caption": "x",
                    "action": "draft",
                    "post_type": "story",
                }
            ),
            content_type="application/json",
        )
        assert r.status_code == 403, r.content
