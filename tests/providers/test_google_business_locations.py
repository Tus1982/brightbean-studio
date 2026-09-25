"""Google Business: every store listing becomes its own selectable account, and listing locations sends a readMask."""

from unittest.mock import MagicMock

from providers.google_business import LOCATION_READ_MASK, GoogleBusinessProvider


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


def _provider(responses):
    p = GoogleBusinessProvider.__new__(GoogleBusinessProvider)
    p.credentials = {}
    calls = []

    def fake_request(method, url, *, access_token=None, params=None, **kw):
        calls.append((url, params))
        return _resp(responses(url, params))

    p._request = fake_request
    return p, calls


def test_get_user_pages_lists_every_location_of_every_account_with_read_mask():
    def responses(url, params):
        if url.endswith("/accounts"):
            return {"accounts": [{"name": "accounts/1"}, {"name": "accounts/2"}]}
        if url.endswith("accounts/1/locations"):
            if params.get("pageToken") == "p2":
                return {"locations": [{"name": "locations/12", "title": "Willy Pisa"}]}
            return {
                "locations": [
                    {
                        "name": "locations/11",
                        "title": "Willy Lucca",
                        "storefrontAddress": {"addressLines": ["Via degli Orafi 26"], "locality": "Lucca"},
                    }
                ],
                "nextPageToken": "p2",
            }
        return {"locations": [{"name": "locations/21", "title": "Willy Genova"}]}

    p, calls = _provider(responses)
    pages = p.get_user_pages("tok")

    assert [x["id"] for x in pages] == ["accounts/1/locations/11", "accounts/1/locations/12", "accounts/2/locations/21"]
    assert pages[0]["name"] == "Willy Lucca - Via degli Orafi 26, Lucca"
    assert all(x["access_token"] == "" for x in pages)
    location_calls = [params for url, params in calls if url.endswith("/locations")]
    assert location_calls and all(params["readMask"] == LOCATION_READ_MASK for params in location_calls)


def test_get_location_id_uses_the_connected_store():
    p, calls = _provider(lambda url, params: {})
    p.credentials = {"account_id": "accounts/1", "location_id": "accounts/1/locations/11"}
    assert p._get_location_id("tok", p._get_account_id("tok")) == "accounts/1/locations/11"
    assert calls == []
