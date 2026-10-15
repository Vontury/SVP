import httpx
import pytest

from app import metadata
from app.metadata import (
    Metadata,
    MetadataError,
    clean_text,
    clean_thumbnail,
    collect_hashtags,
    decide_outcome,
    fetch_metadata,
    fetch_with_oembed,
    from_oembed,
    from_ytdlp_info,
    merge,
    is_placeholder_title,
)

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_clean_text_removes_control_characters_and_trims():
    assert clean_text("  a\x00b\x07 \n", 10) == "ab"


def test_clean_text_applies_the_limit():
    assert clean_text("abcdef", 3) == "abc"


@pytest.mark.parametrize("value", [None, 5, "", "   "])
def test_clean_text_rejects_empty_or_non_text(value):
    assert clean_text(value, 10) is None


def test_hashtags_are_lowercased_deduplicated_and_in_order():
    found = collect_hashtags("Fun #Cats #cats #Mèo #a.b", None, "#More")
    assert found == ("cats", "mèo", "a", "more")


def test_hashtags_are_capped():
    text = " ".join(f"#t{i}" for i in range(100))
    assert len(collect_hashtags(text)) == 30


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://i.ytimg.com/x.jpg", "https://i.ytimg.com/x.jpg"),
        ("http://i.ytimg.com/x.jpg", None),
        ("https://x/y z.jpg", None),
        ("https://x/" + "a" * 3000, None),
        (None, None),
    ],
)
def test_thumbnail_must_be_a_short_https_link(value, expected):
    assert clean_thumbnail(value) == expected


def test_ytdlp_info_is_mapped():
    info = {
        "title": "Hello #fun",
        "description": "Desc #Fun #more",
        "thumbnail": "https://i.ytimg.com/x.jpg",
    }
    assert from_ytdlp_info(info) == Metadata(
        "Hello #fun", "Desc #Fun #more", ("fun", "more"), "https://i.ytimg.com/x.jpg"
    )


@pytest.mark.parametrize("info", [None, "text", 5])
def test_ytdlp_info_that_is_not_a_dict_gives_empty_metadata(info):
    assert from_ytdlp_info(info) == Metadata()


def test_oembed_data_is_mapped():
    data = {"title": "Cap #x", "thumbnail_url": "https://p/t.jpg", "author_name": "n"}
    assert from_oembed(data) == Metadata("Cap #x", None, ("x",), "https://p/t.jpg")


def test_merge_prefers_the_primary_and_fills_gaps():
    merged = merge(
        Metadata(title="a"),
        Metadata(title="b", thumbnail_url="https://t", hashtags=("z",)),
    )
    assert merged == Metadata("a", None, ("z",), "https://t")


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "FAILED"),
        (Metadata(), "FAILED"),
        (Metadata(description="only a description"), "FAILED"),
        (Metadata(title="t"), "PARTIAL"),
        (Metadata(thumbnail_url="https://a"), "PARTIAL"),
        (Metadata(title="t", thumbnail_url="https://a"), "COMPLETED"),
    ],
)
def test_outcome_depends_on_title_and_thumbnail(value, expected):
    assert decide_outcome(value) == expected


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_oembed_success():
    def handler(request):
        assert request.url.host == "www.youtube.com"
        assert request.url.params["url"] == YT
        return httpx.Response(
            200, json={"title": "Song #music", "thumbnail_url": "https://i.ytimg.com/t.jpg"}
        )

    result = fetch_with_oembed(YT, "YOUTUBE", client_for(handler))
    assert result == Metadata("Song #music", None, ("music",), "https://i.ytimg.com/t.jpg")


def test_oembed_uses_the_tiktok_endpoint_for_tiktok():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={"title": "Caption"})

    fetch_with_oembed("https://www.tiktok.com/@u/video/1234567890", "TIKTOK", client_for(handler))
    assert hosts == ["www.tiktok.com"]


def not_found(request):
    return httpx.Response(404)


def bad_json(request):
    return httpx.Response(200, text="not json")


def too_big(request):
    return httpx.Response(200, content=b"x" * 300_000)


def network_down(request):
    raise httpx.ConnectError("down")


@pytest.mark.parametrize("handler", [not_found, bad_json, too_big, network_down])
def test_oembed_failures_give_empty_metadata(handler):
    assert fetch_with_oembed(YT, "YOUTUBE", client_for(handler)) == Metadata()


def test_complete_primary_result_skips_the_fallback(monkeypatch):
    complete = Metadata("T", thumbnail_url="https://t")
    fallback_calls = []

    def fallback(url, platform, client=None):
        fallback_calls.append(url)
        return Metadata()

    monkeypatch.setattr(metadata, "fetch_with_ytdlp", lambda url: complete)
    monkeypatch.setattr(metadata, "fetch_with_oembed", fallback)
    assert fetch_metadata(YT, "YOUTUBE") == complete
    assert fallback_calls == []


def test_fallback_fills_in_missing_parts(monkeypatch):
    monkeypatch.setattr(metadata, "fetch_with_ytdlp", lambda url: Metadata(title="Title"))
    monkeypatch.setattr(
        metadata,
        "fetch_with_oembed",
        lambda url, platform, client=None: Metadata(title="Other", thumbnail_url="https://t"),
    )
    assert fetch_metadata(YT, "YOUTUBE") == Metadata("Title", None, (), "https://t")


def test_nothing_found_raises_a_coded_error(monkeypatch):
    monkeypatch.setattr(metadata, "fetch_with_ytdlp", lambda url: Metadata())
    monkeypatch.setattr(
        metadata, "fetch_with_oembed", lambda url, platform, client=None: Metadata()
    )
    with pytest.raises(MetadataError) as exc:
        fetch_metadata(YT, "YOUTUBE")
    assert exc.value.code == "METADATA_UNAVAILABLE"

@pytest.mark.parametrize(
    "title",
    [
        "youtube video #e2eTestAB01",
        "YouTube video #abc-123",
        "tiktok video #1234567890",
        "  youtube video #x  ",
    ],
)
def test_placeholder_titles_are_recognised(title):
    assert is_placeholder_title(title) is True


@pytest.mark.parametrize(
    "title",
    ["My youtube video #fun", "Cats being cats", None, 5, "youtube video"],
)
def test_real_titles_are_not_placeholders(title):
    assert is_placeholder_title(title) is False


def test_ytdlp_info_with_a_placeholder_title_is_discarded():
    info = {
        "title": "youtube video #e2eTestAB01",
        "thumbnail": "https://i.ytimg.com/vi/e2eTestAB01/maxresdefault.jpg",
    }
    assert from_ytdlp_info(info) == Metadata()