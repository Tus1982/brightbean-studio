"""Google Business Profile API provider implementation."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from urllib.parse import urlencode

from .base import SocialProvider
from .exceptions import APIError, OAuthError, PublishError
from .types import (
    AccountMetrics,
    AccountProfile,
    AuthType,
    MediaType,
    OAuthTokens,
    PostMetrics,
    PostType,
    PublishContent,
    PublishResult,
)

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ACCOUNTS_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
# Business Information API v1 rejects locations.list without a readMask (400 "read_mask: Field is required").
LOCATION_READ_MASK = "name,title,storefrontAddress,phoneNumbers"
POSTS_API = "https://mybusiness.googleapis.com/v4"


class GoogleBusinessProvider(SocialProvider):
    """Google Business Profile provider.

    Uses Google OAuth 2.0.  The ``credentials`` dict must contain:

    - ``client_id``
    - ``client_secret``

    Optional:
    - ``account_id`` – Google Business account ID
    - ``location_id`` – Google Business location ID
    """

    # Publishes from hosted URLs only — the platform fetches the media
    # itself, so ``PublishContent.media_files`` is never read and the engine
    # can skip downloading the asset to local disk entirely.
    needs_local_media = False

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "Google Business"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 1500

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.TEXT, PostType.IMAGE]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG]

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/business.manage"]

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        """Build the Google OAuth 2.0 authorization URL."""
        params = {
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.required_scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        """Exchange an authorization code for Google access/refresh tokens."""
        resp = self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Token exchange failed: {data.get('error_description', data['error'])}",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        """Refresh an expired Google access token."""
        resp = self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Token refresh failed: {data.get('error_description', data['error'])}",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=refresh_token,  # Google doesn't rotate refresh tokens
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    def revoke_token(self, access_token: str) -> bool:
        """Revoke a Google OAuth token."""
        try:
            self._request(
                "POST",
                REVOKE_URL,
                params={"token": access_token},
            )
            return True
        except Exception:
            logger.exception("Failed to revoke Google token")
            return False

    # ------------------------------------------------------------------
    # Account / location helpers
    # ------------------------------------------------------------------

    def _get_account_id(self, access_token: str) -> str:
        """Return the account ID from credentials or by listing accounts."""
        if self.credentials.get("account_id"):
            return self.credentials["account_id"]

        resp = self._request(
            "GET",
            f"{ACCOUNTS_API}/accounts",
            access_token=access_token,
        )
        data = resp.json()
        accounts = data.get("accounts", [])
        if not accounts:
            raise PublishError(
                "No Google Business accounts found",
                platform=self.platform_name,
            )
        # Return the first account's resource name (e.g. "accounts/123")
        return accounts[0]["name"]

    def _get_location_id(self, access_token: str, account_id: str) -> str:
        """Return the location ID from credentials or by listing locations."""
        if self.credentials.get("location_id"):
            return self.credentials["location_id"]

        resp = self._request(
            "GET",
            f"{BUSINESS_INFO_API}/{account_id}/locations",
            access_token=access_token,
            params={"readMask": LOCATION_READ_MASK, "pageSize": 100},
        )
        data = resp.json()
        locations = data.get("locations", [])
        if not locations:
            raise PublishError(
                "No locations found for Google Business account",
                platform=self.platform_name,
            )
        # The v4 localPosts endpoint wants the full "accounts/X/locations/Y" path.
        return f"{account_id}/{locations[0]['name']}"

    def _list_accounts(self, access_token: str) -> list[dict]:
        accounts: list[dict] = []
        page_token = None
        while True:
            params = {"pageSize": 20}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{ACCOUNTS_API}/accounts", access_token=access_token, params=params).json()
            accounts.extend(data.get("accounts", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return accounts

    def _list_locations(self, access_token: str, account_id: str) -> list[dict]:
        locations: list[dict] = []
        page_token = None
        while True:
            params = {"readMask": LOCATION_READ_MASK, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            data = self._request(
                "GET", f"{BUSINESS_INFO_API}/{account_id}/locations", access_token=access_token, params=params
            ).json()
            locations.extend(data.get("locations", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return locations

    def get_user_pages(self, access_token: str) -> list[dict]:
        """Every location (store) the user manages, across all accounts, for the account-selection step.

        One SocialAccount per location: id is the full "accounts/X/locations/Y" path the v4 posts API needs.
        Google has no per-location token, so access_token is left empty (the user token is used).
        """
        pages: list[dict] = []
        seen: set[str] = set()
        for account in self._list_accounts(access_token):
            account_id = account.get("name", "")
            if not account_id:
                continue
            try:
                locations = self._list_locations(access_token, account_id)
            except APIError:
                logger.warning("Google Business: cannot list locations of %s", account_id, exc_info=True)
                continue
            for loc in locations:
                loc_name = loc.get("name", "")
                if not loc_name:
                    continue
                full_id = f"{account_id}/{loc_name}"
                if full_id in seen:
                    continue
                seen.add(full_id)
                address = loc.get("storefrontAddress", {})
                parts = [*address.get("addressLines", []), address.get("locality", "")]
                where = ", ".join(p for p in parts if p)
                title = loc.get("title", loc_name)
                pages.append(
                    {
                        "id": full_id,
                        "name": f"{title} - {where}" if where else title,
                        "access_token": "",
                        "category": "Google Business Profile",
                        "picture": None,
                        "followers_count": 0,
                    }
                )
        return pages

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        """Read the selected store only; never rename anything on Google."""
        account_id = self._get_account_id(access_token)
        location_id = self._get_location_id(access_token, account_id)
        location = location_id[location_id.index("locations/") :]
        loc = self._request(
            "GET", f"{BUSINESS_INFO_API}/{location}", access_token=access_token, params={"readMask": LOCATION_READ_MASK}
        ).json()
        if loc.get("name") != location:
            raise APIError("Google returned no selected location data", platform=self.platform_name)
        address_obj = loc.get("storefrontAddress") or {}
        address = ", ".join(p for p in [*address_obj.get("addressLines", []), address_obj.get("locality", "")] if p)
        title = loc.get("title") or location
        return AccountProfile(
            platform_id=location_id,
            name=f"{title} - {address}" if address else title,
            extra={
                "address": address,
                "locality": address_obj.get("locality"),
                "phone": (loc.get("phoneNumbers") or {}).get("primaryPhone", ""),
            },
        )

    def _selected_location(self):
        location = str(self.credentials.get("location_id") or "")
        if not re.fullmatch(r"accounts/[^/]+/locations/[^/]+", location):
            raise APIError("Selected Google location_id missing or invalid", platform=self.platform_name)
        return location

    def get_reviews(self, access_token: str, limite: int = 20):
        """One bounded page of reviews and the platform's overall rating."""
        limit = max(1, min(int(limite), 50))
        location = self._selected_location()
        body = self._request(
            "GET",
            f"{POSTS_API}/{location}/reviews",
            access_token=access_token,
            params={"pageSize": limit, "orderBy": "updateTime desc"},
        ).json()
        if not isinstance(body, dict) or not any(k in body for k in ("reviews", "totalReviewCount")):
            raise APIError("Google returned no review data", platform=self.platform_name)
        return {
            "location_id": location,
            "average_rating": body.get("averageRating"),
            "total_reviews": body.get("totalReviewCount"),
            "reviews": body.get("reviews", [])[:limit],
            "limite": limit,
            "troncato": bool(body.get("nextPageToken")) or len(body.get("reviews", [])) > limit,
        }

    def get_account_metrics(self, access_token: str, date_range: tuple[datetime, datetime]) -> AccountMetrics:
        """Business Profile Performance API: actual daily store visits and website clicks."""
        location = self._selected_location().split("/", 2)[2]
        requested = [
            "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
            "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
            "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
            "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
            "WEBSITE_CLICKS",
        ]
        params = {"dailyMetrics": requested}
        for key, instant in zip(("start_date", "end_date"), date_range, strict=True):
            for part in ("year", "month", "day"):
                params[f"dailyRange.{key}.{part}"] = getattr(instant, part)
        body = self._request(
            "GET",
            f"https://businessprofileperformance.googleapis.com/v1/{location}:fetchMultiDailyMetricsTimeSeries",
            access_token=access_token,
            params=params,
        ).json()
        metrics: dict[str, int] = {}
        observed = set()
        for group in body.get("multiDailyMetricTimeSeries", []):
            for series in group.get("dailyMetricTimeSeries", []):
                metric = series.get("dailyMetric")
                if metric not in requested:
                    continue
                values = []
                for row in (series.get("timeSeries") or {}).get("datedValues", []):
                    day = date(**row["date"])
                    if date_range[0].date() <= day <= date_range[1].date():
                        values.append(int(row.get("value", 0)))
                if values:
                    observed.add(metric)
                    key = "clicks" if metric == "WEBSITE_CLICKS" else "impressions"
                    metrics[key] = metrics.get(key, 0) + sum(values)
        if observed != set(requested):
            raise APIError(
                "Google returned no daily metric data (possibly not finalized yet)", platform=self.platform_name
            )
        return AccountMetrics(followers=None, impressions=metrics.get("impressions", 0), extra={"metrics": metrics})

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        """Publish a local post to Google Business Profile."""
        if content.text and len(content.text) > self.max_caption_length:
            raise PublishError(
                f"Post text exceeds {self.max_caption_length} characters (got {len(content.text)})",
                platform=self.platform_name,
                retryable=False,
            )

        account_id = self._get_account_id(access_token)
        location_id = self._get_location_id(access_token, account_id)

        # Determine topic type
        topic_type = content.extra.get("topic_type", "STANDARD")

        body: dict = {
            "languageCode": content.extra.get("language_code", "it"),
            "summary": content.text or "",
            "topicType": topic_type,
        }

        # [Willy 26/09/2026] The destination link is the post's button on Maps / Search.
        if content.link_url:
            body["callToAction"] = {
                "actionType": content.extra.get("cta_type", "LEARN_MORE"),
                "url": content.link_url,
            }

        # Attach media
        if content.media_urls:
            body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": url} for url in content.media_urls]

        # EVENT type extras
        if topic_type == "EVENT" and content.extra.get("event"):
            body["event"] = content.extra["event"]

        # OFFER type extras
        if topic_type == "OFFER" and content.extra.get("offer"):
            body["offer"] = content.extra["offer"]

        resp = self._request(
            "POST",
            f"{POSTS_API}/{location_id}/localPosts",
            access_token=access_token,
            json=body,
        )
        data = resp.json()

        post_name = data.get("name", "")
        return PublishResult(
            platform_post_id=post_name,
            url=data.get("searchUrl"),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        """Fetch metrics for a Google Business local post."""
        resp = self._request(
            "GET",
            f"{POSTS_API}/{post_id}",
            access_token=access_token,
        )
        data = resp.json()
        search_views = 0
        maps_views = 0
        for metric in data.get("searchActionMetrics", []):
            if metric.get("metricType") == "QUERIES_DIRECT":
                search_views += metric.get("value", 0)
        return PostMetrics(
            impressions=search_views + maps_views,
            extra={
                "search_views": search_views,
                "maps_views": maps_views,
                "raw": data,
            },
        )
