"""Video Pin: register, multipart upload with the signed parameters, wait, then the Pin."""

from unittest.mock import MagicMock, patch

from providers.pinterest import PinterestProvider
from providers.types import PostType, PublishContent


def _resp(body):
    r = MagicMock()
    r.json.return_value = body
    return r


def test_video_pin_uploads_multipart_waits_and_uses_key_frame(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4")
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw))
        if url.endswith("/media") and method == "POST":
            return _resp({"media_id": "m1", "upload_url": "https://bucket/up",
                          "upload_parameters": {"key": "k", "policy": "p"}})
        if url == "https://bucket/up":
            return _resp({})
        if url.endswith("/media/m1"):
            return _resp({"status": "succeeded"})
        if url.endswith("/pins"):
            return _resp({"id": "pin9"})
        raise AssertionError(url)

    provider = PinterestProvider.__new__(PinterestProvider)
    content = PublishContent(text="t", title="T", description="d", media_files=[str(video)],
                             media_urls=["https://x/clip.mp4"], post_type=PostType.PIN,
                             extra={"board_id": "b1"}, link_url="https://willybesmart.com/p")
    with patch.object(PinterestProvider, "_request", side_effect=fake_request), \
            patch("providers.pinterest.time.sleep"):
        res = provider.publish_post("tok", content)

    assert res.platform_post_id == "pin9"
    upload = next(c for c in calls if c[1] == "https://bucket/up")
    assert upload[0] == "POST"
    assert upload[2]["data"] == {"key": "k", "policy": "p"}
    assert "file" in upload[2]["files"]
    pin = next(c for c in calls if c[1].endswith("/pins"))
    assert pin[2]["json"]["media_source"] == {
        "source_type": "video_id", "media_id": "m1", "cover_image_key_frame_time": 1}
    assert pin[2]["json"]["link"] == "https://willybesmart.com/p"
