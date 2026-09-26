"""Pinterest API v5 provider."""

from __future__ import annotations

import base64
import logging
import os
import time
from urllib.parse import urlencode

from .base import SocialProvider
from .exceptions import OAuthError, PublishError
from .types import (
    AccountProfile,
    AuthType,
    MediaType,
    OAuthTokens,
    PostMetrics,
    PostType,
    PublishContent,
    PublishResult,
    RateLimitConfig,
)

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.pinterest.com/oauth/"
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
API_BASE = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5")
TOKEN_URL = f"{API_BASE}/oauth/token"


class PinterestProvider(SocialProvider):
    """Pinterest API v5 provider using OAuth 2.0."""

    def __init__(self, credentials: dict | None = None):
        creds = dict(credentials or {})
        # Normalize: accept app_id/app_secret as aliases for client_id/client_secret
        if "app_id" in creds and "client_id" not in creds:
            creds["client_id"] = creds.pop("app_id")
        if "app_secret" in creds and "client_secret" not in creds:
            creds["client_secret"] = creds.pop("app_secret")
        super().__init__(creds)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "Pinterest"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 500

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.PIN]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG, MediaType.GIF, MediaType.MP4]

    @property
    def required_scopes(self) -> list[str]:
        # boards:write: Pinterest lo chiede anche per POST /pins (401 «Missing: [boards:write]», 26/09/2026)
        return ["user_accounts:read", "boards:read", "boards:write", "pins:read", "pins:write"]

    @property
    def rate_limits(self) -> RateLimitConfig:
        return RateLimitConfig(
            requests_per_hour=1000,
            requests_per_day=24000,
            publish_per_day=25,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _basic_auth_header(self) -> dict[str, str]:
        """Build HTTP Basic auth header for token endpoints."""
        client_id = self.credentials["client_id"]
        client_secret = self.credentials["client_secret"]
        encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        params = {
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.required_scopes),
            "response_type": "code",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        resp = self._request(
            "POST",
            TOKEN_URL,
            headers=self._basic_auth_header(),
            data={
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"Pinterest token exchange failed: {body}",
                platform=self.platform_name,
                raw_response=body,
            )
        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_in=body.get("expires_in"),
            scope=body.get("scope"),
            raw_response=body,
        )

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        resp = self._request(
            "POST",
            TOKEN_URL,
            headers=self._basic_auth_header(),
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"Pinterest token refresh failed: {body}",
                platform=self.platform_name,
                raw_response=body,
            )
        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", refresh_token),
            expires_in=body.get("expires_in"),
            scope=body.get("scope"),
            raw_response=body,
        )

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        resp = self._request(
            "GET",
            f"{API_BASE}/user_account",
            access_token=access_token,
        )
        body = resp.json()
        return AccountProfile(
            platform_id=body.get("id", ""),
            name=body.get("business_name") or body.get("username", ""),
            handle=body.get("username"),
            avatar_url=body.get("profile_image"),
            follower_count=body.get("follower_count", 0),
            extra=body,
        )

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        board_id = content.extra.get("board_id")
        if not board_id:
            raise PublishError(
                "board_id is required in content.extra for Pinterest pins",
                platform=self.platform_name,
            )

        payload: dict = {
            "board_id": board_id,
            "description": (content.description or content.text or "")[: self.max_caption_length],
        }

        if content.title:
            payload["title"] = content.title[:100]

        if content.link_url:
            payload["link"] = content.link_url

        alt_text = content.extra.get("alt_text")
        if alt_text:
            payload["alt_text"] = alt_text[:500]

        # Determine media source
        # [26/09/2026] Nobody sets ``is_video`` for Pinterest (the engine resolves every
        # Pinterest post to PIN), so a video attachment is recognised by its file.
        first = (content.media_files[:1] or content.media_urls[:1] or [""])[0].lower().split("?")[0]
        is_video = content.extra.get("is_video", False) or first.endswith(VIDEO_EXTS)

        if is_video:
            return self._publish_video_pin(access_token, content, payload)

        # Image pin
        if content.media_urls:
            payload["media_source"] = {
                "source_type": "image_url",
                "url": content.media_urls[0],
            }
        elif content.media_files:
            raise PublishError(
                "Pinterest image file upload not supported via this provider; "
                "use media_urls with a hosted image URL instead",
                platform=self.platform_name,
            )
        else:
            raise PublishError(
                "No media provided for Pinterest pin",
                platform=self.platform_name,
            )

        resp = self._request(
            "POST",
            f"{API_BASE}/pins",
            access_token=access_token,
            json=payload,
        )
        body = resp.json()
        pin_id = body.get("id", "")
        return PublishResult(
            platform_post_id=pin_id,
            url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            extra=body,
        )

    def _publish_video_pin(
        self,
        access_token: str,
        content: PublishContent,
        payload: dict,
    ) -> PublishResult:
        """Video Pin, the three steps Pinterest v5 requires.

        [26/09/2026 Willy] The old version PUT the bytes to ``upload_url`` and created the Pin
        straight away: Pinterest wants a multipart POST carrying ``upload_parameters``, then the
        media must reach ``succeeded`` before a Pin can reference it, and a video Pin needs a
        cover (here a key frame).
        """
        if not content.media_files:
            raise PublishError("No video file for Pinterest video pin", platform=self.platform_name)

        # Step 1: register the upload
        media_body = self._request(
            "POST", f"{API_BASE}/media", access_token=access_token, json={"media_type": "video"}
        ).json()
        media_id = media_body.get("media_id", "")
        upload_url = media_body.get("upload_url")
        if not media_id or not upload_url:
            raise PublishError(f"Pinterest media register failed: {media_body}", platform=self.platform_name)

        # Step 2: multipart POST to the signed bucket, parameters first, file last
        with open(content.media_files[0], "rb") as f:
            video_data = f.read()
        self._request(
            "POST",
            upload_url,
            data=dict(media_body.get("upload_parameters") or {}),
            files={"file": ("video.mp4", video_data, "video/mp4")},
            timeout=300.0,
        )

        # Step 3: wait for Pinterest to process it
        status = ""
        for _ in range(60):
            status = (
                self._request("GET", f"{API_BASE}/media/{media_id}", access_token=access_token)
                .json()
                .get("status", "")
            )
            if status in ("succeeded", "failed"):
                break
            time.sleep(5)
        if status != "succeeded":
            raise PublishError(f"Pinterest video processing: {status or 'timeout'}", platform=self.platform_name)

        payload["media_source"] = {
            "source_type": "video_id",
            "media_id": media_id,
            "cover_image_key_frame_time": 1,
        }
        body = self._request("POST", f"{API_BASE}/pins", access_token=access_token, json=payload).json()
        pin_id = body.get("id", "")
        return PublishResult(
            platform_post_id=pin_id,
            url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            extra=body,
        )

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    def get_boards(self, access_token: str) -> list[dict]:
        """Fetch all boards for the authenticated account."""
        resp = self._request(
            "GET",
            f"{API_BASE}/boards",
            access_token=access_token,
        )
        body = resp.json()
        return body.get("items", [])

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        resp = self._request(
            "GET",
            f"{API_BASE}/pins/{post_id}/analytics",
            access_token=access_token,
            params={
                "metric_types": "IMPRESSION,PIN_CLICK,SAVE,OUTBOUND_CLICK",
                "start_date": "2020-01-01",
                "end_date": "2099-12-31",
            },
        )
        body = resp.json()
        # Pinterest returns metrics as aggregated daily data
        all_data = body.get("all", {})
        return PostMetrics(
            impressions=all_data.get("IMPRESSION", 0),
            clicks=all_data.get("PIN_CLICK", 0),
            saves=all_data.get("SAVE", 0),
            extra={
                "outbound_clicks": all_data.get("OUTBOUND_CLICK", 0),
                "raw": body,
            },
        )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def revoke_token(self, access_token: str) -> bool:
        # Pinterest does not support token revocation; tokens expire naturally.
        return False
