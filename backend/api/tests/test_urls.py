import httpx
import pytest

from app.urls import (
    MAX_REDIRECTS,
    TIKTOK,
    YOUTUBE,
    UrlRejected,
    _host_is_public,
    resolve_short_link,
    validate_url,
)


@pytest.mark.parametrize(
    "raw, platform, canonical, short",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx&t=10", YOUTUBE,
         "https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", YOUTUBE,
         "https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("https://m.youtube.com/shorts/abcdefghijk", YOUTUBE,
         "https://www.youtube.com/shorts/abcdefghijk", False),
        ("  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ", YOUTUBE,
         "https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("https://www.tiktok.com/@some.user/video/7234567890123456789?_r=1&u_code=xyz",
         TIKTOK, "https://www.tiktok.com/@some.user/video/7234567890123456789", False),
        ("https://tiktok.com/@a_b/video/1234567890", TIKTOK,
         "https://www.tiktok.com/@a_b/video/1234567890", False),
        ("https://vm.tiktok.com/ZMabc123/", TIKTOK, "https://vm.tiktok.com/ZMabc123/", True),
        ("https://www.tiktok.com/t/ZTabc123", TIKTOK,
         "https://www.tiktok.com/t/ZTabc123/", True),
    ],
)
def test_accepts_and_cleans(raw, platform, canonical, short):
    v = validate_url(raw)
    assert (v.platform, v.canonical_url, v.needs_resolution) == (platform, canonical, short)


@pytest.mark.parametrize(
    "raw, code",
    [
        ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "NOT_HTTPS"),
        ("javascript:alert(1)", "NOT_HTTPS"),
        ("https://evil.com/watch?v=dQw4w9WgXcQ", "UNSUPPORTED_HOST"),
        ("https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ", "UNSUPPORTED_HOST"),
        ("https://www.youtube.com@evil.com/watch?v=dQw4w9WgXcQ", "CREDENTIALS_IN_URL"),
        ("https://youtube.com:8443/watch?v=dQw4w9WgXcQ", "UNSUPPORTED_HOST"),
        ("https://localhost/watch?v=dQw4w9WgXcQ", "UNSUPPORTED_HOST"),
        ("https://127.0.0.1/", "UNSUPPORTED_HOST"),
        ("https://169.254.169.254/latest/meta-data", "UNSUPPORTED_HOST"),
        ("https://www.youtube.com/playlist?list=PL123", "UNSUPPORTED_PATH"),
        ("https://www.youtube.com/watch?v=short", "UNSUPPORTED_PATH"),
        ("https://www.youtube.com/watch", "UNSUPPORTED_PATH"),
        ("https://www.tiktok.com/@user", "UNSUPPORTED_PATH"),
        ("https://www.tiktok.com/@user/video/12", "UNSUPPORTED_PATH"),
        ("https://www.youtube.com/watch?v=dQw4w9\nXcQ", "INVALID_URL"),
        ("", "INVALID_URL"),
        ("x" * 3000, "INVALID_URL"),
        ("https://", "INVALID_URL"),
    ],
)
def test_rejects(raw, code):
    with pytest.raises(UrlRejected) as exc:
        validate_url(raw)
    assert exc.value.code == code


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1"]
)
def test_private_addresses_are_not_public(host):
    assert _host_is_public(host) is False

SHORT = "https://vm.tiktok.com/ZMabc123/"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def public(host):
    return True


def test_short_link_resolves_to_clean_video_url():
    def handler(request):
        return httpx.Response(
            301, headers={"location": "https://www.tiktok.com/@u/video/1234567890?x=1"}
        )

    result = resolve_short_link(SHORT, client_for(handler), public)
    assert result.canonical_url == "https://www.tiktok.com/@u/video/1234567890"
    assert result.needs_resolution is False


def test_redirect_to_another_site_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(UrlRejected) as exc:
        resolve_short_link(SHORT, client_for(handler), public)
    assert exc.value.code == "REDIRECT_BLOCKED"


def test_redirect_loop_is_cut_off():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(302, headers={"location": "https://vm.tiktok.com/ZZZZ/"})

    with pytest.raises(UrlRejected) as exc:
        resolve_short_link(SHORT, client_for(handler), public)
    assert exc.value.code == "REDIRECT_TOO_MANY"
    assert len(calls) == MAX_REDIRECTS + 1


def test_non_redirect_response_is_unresolved():
    def handler(request):
        return httpx.Response(200, text="hello")

    with pytest.raises(UrlRejected) as exc:
        resolve_short_link(SHORT, client_for(handler), public)
    assert exc.value.code == "SHORT_LINK_UNRESOLVED"


def test_network_error_is_unresolved():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(UrlRejected) as exc:
        resolve_short_link(SHORT, client_for(handler), public)
    assert exc.value.code == "SHORT_LINK_UNRESOLVED"


def test_non_public_host_is_never_contacted():
    def handler(request):
        raise AssertionError("must not be called")

    with pytest.raises(UrlRejected) as exc:
        resolve_short_link(SHORT, client_for(handler), lambda host: False)
    assert exc.value.code == "HOST_BLOCKED"


def test_direct_url_needs_no_network():
    def handler(request):
        raise AssertionError("must not be called")

    result = resolve_short_link(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", client_for(handler), public
    )
    assert result.platform == YOUTUBE