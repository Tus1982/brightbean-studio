"""``GET /api/v1/accounts`` — list the connected accounts this key may target."""

from __future__ import annotations

from ninja import Router

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.api.schemas import AccountsListResponse, AccountSummary

router = Router(tags=["accounts"])


@router.get(
    "/",
    response=AccountsListResponse,
    summary="List the SocialAccounts this API key is allowed to act on",
)
def list_accounts(request):
    enforce_http_rate_limits(request, is_write=False)
    api_key = request.api_key
    accounts = [AccountSummary.from_social_account(sa) for sa in api_key.social_accounts.all()]
    log_audit_entry(request, action="accounts.list", target_id=None, status_code=200)
    return AccountsListResponse(accounts=accounts)


@router.get(
    "/{account_id}/pinterest/boards",
    summary="List the Pinterest boards of a connected Pinterest account",
)
def pinterest_boards(request, account_id: str):
    """The boards a Pin can go to. The API needs them because creating a Pinterest
    post requires ``board_id``; the composer has its own session-only twin."""
    from ninja.errors import HttpError

    from apps.credentials.models import resolve_platform_credentials
    from providers import get_provider

    enforce_http_rate_limits(request, is_write=False)
    account = request.api_key.social_accounts.filter(id=account_id, platform="pinterest").first()
    if account is None:
        raise HttpError(404, "Pinterest account not found among this key's accounts.")
    credentials = resolve_platform_credentials("pinterest", account.workspace.organization_id)
    provider = get_provider("pinterest", credentials)
    access_token = account.oauth_access_token
    if account.token_expires_at and account.is_token_expiring_soon:
        try:
            access_token = account.refresh_oauth_token(provider)
        except Exception as exc:
            raise HttpError(502, "Token refresh failed") from exc
    try:
        boards = provider.get_boards(access_token)
    except Exception as exc:
        raise HttpError(502, "Failed to fetch boards") from exc
    log_audit_entry(request, action="accounts.pinterest_boards", target_id=account.id, status_code=200)
    return {"boards": [{"id": b.get("id"), "name": b.get("name")} for b in boards]}
