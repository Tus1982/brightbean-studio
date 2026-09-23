"""Pinterest via the Agent API: ``board_id`` / ``link_url`` and the boards list. [2026-09-23]

A Pin cannot be published without a board: the provider raises when
``content.extra["board_id"]`` is missing. The composer has a board picker; the
API had nothing, so every Pin created by an agent would have failed at publish
time. What these tests hold still:

* ``board_id`` and ``link_url`` reach ``platform_extra`` (the channel the engine reads);
* a Pinterest post without a board, or without media, is refused at CREATE time;
* the two fields are refused on any other platform instead of silently ignored;
* the boards endpoint only answers for a Pinterest account inside the key's scope.
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




@pytest.fixture
def pinterest_account(db, workspace):
    return _account(workspace, "pinterest", "pi-1")


@pytest.mark.django_db
class TestPinterestBoard:
    def test_board_and_link_land_in_platform_extra(self, client_for, pinterest_account, image_asset):
        client = client_for(pinterest_account)
        r = _create(client, pinterest_account, title="Un Pin", media_asset_ids=[str(image_asset.id)],
                    board_id="board-42", link_url="https://willybesmart.com/products/x")
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get(social_account=pinterest_account)
        assert pp.platform_extra == {"board_id": "board-42", "link_url": "https://willybesmart.com/products/x"}

    def test_pinterest_without_board_is_refused(self, client_for, pinterest_account, image_asset):
        client = client_for(pinterest_account)
        r = _create(client, pinterest_account, title="Un Pin", media_asset_ids=[str(image_asset.id)])
        assert r.status_code == 422
        assert "board_id" in r.content.decode()

    def test_pinterest_without_media_is_refused(self, client_for, pinterest_account):
        client = client_for(pinterest_account)
        r = _create(client, pinterest_account, title="Un Pin", board_id="board-42")
        assert r.status_code == 422

    def test_board_on_other_platform_is_refused(self, client_for, instagram_account, image_asset):
        client = client_for(instagram_account)
        r = _create(client, instagram_account, media_asset_ids=[str(image_asset.id)], board_id="board-42")
        assert r.status_code == 422

    def test_boards_endpoint_outside_scope_is_404(self, client_for, instagram_account, pinterest_account):
        client = client_for(instagram_account)
        r = client.get(f"/api/v1/accounts/{pinterest_account.id}/pinterest/boards")
        assert r.status_code == 404

    def test_boards_endpoint_lists_boards(self, client_for, pinterest_account, monkeypatch):
        from providers.pinterest import PinterestProvider

        monkeypatch.setattr(PinterestProvider, "get_boards",
                            lambda self, token: [{"id": "b1", "name": "Cartoleria e scrivania"}])
        client = client_for(pinterest_account)
        r = client.get(f"/api/v1/accounts/{pinterest_account.id}/pinterest/boards")
        assert r.status_code == 200, r.content
        assert r.json() == {"boards": [{"id": "b1", "name": "Cartoleria e scrivania"}]}
