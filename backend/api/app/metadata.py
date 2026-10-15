import re
from dataclasses import dataclass

import httpx

TITLE_MAX = 500
DESCRIPTION_MAX = 5000
HASHTAG_MAX = 30
HASHTAG_LENGTH_MAX = 50
THUMBNAIL_MAX = 2048
OEMBED_MAX_BYTES = 200_000
YTDLP_TIMEOUT_SECONDS = 20
OEMBED_TIMEOUT_SECONDS = 10

_OEMBED_ENDPOINTS = {
    "YOUTUBE": "https://www.youtube.com/oembed",
    "TIKTOK": "https://www.tiktok.com/oembed",
}
_HASHTAG = re.compile(r"#(\w{1,50})")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PLACEHOLDER_TITLE = re.compile(r"(youtube|tiktok) video #?[\w-]+", re.IGNORECASE)


class MetadataError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Metadata:
    title: str | None = None
    description: str | None = None
    hashtags: tuple = ()
    thumbnail_url: str | None = None


def clean_text(value, limit):
    if not isinstance(value, str):
        return None
    text = _CONTROL.sub("", value).strip()
    if not text:
        return None
    return text[:limit]

def is_placeholder_title(title):
    return isinstance(title, str) and bool(_PLACEHOLDER_TITLE.fullmatch(title.strip()))

def clean_thumbnail(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > THUMBNAIL_MAX or not value.startswith("https://"):
        return None
    if any(ord(c) < 33 for c in value):
        return None
    return value


def collect_hashtags(*texts):
    found = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for tag in _HASHTAG.findall(text):
            tag = tag.lower()
            if tag not in found:
                found.append(tag)
            if len(found) >= HASHTAG_MAX:
                return tuple(found)
    return tuple(found)


def from_ytdlp_info(info):
    if not isinstance(info, dict) or is_placeholder_title(info.get("title")):
        return Metadata()
    title = clean_text(info.get("title"), TITLE_MAX)
    description = clean_text(info.get("description"), DESCRIPTION_MAX)
    return Metadata(
        title=title,
        description=description,
        hashtags=collect_hashtags(title, description),
        thumbnail_url=clean_thumbnail(info.get("thumbnail")),
    )


def from_oembed(data):
    if not isinstance(data, dict):
        return Metadata()
    title = clean_text(data.get("title"), TITLE_MAX)
    return Metadata(
        title=title,
        hashtags=collect_hashtags(title),
        thumbnail_url=clean_thumbnail(data.get("thumbnail_url")),
    )


def merge(primary, fallback):
    return Metadata(
        title=primary.title or fallback.title,
        description=primary.description or fallback.description,
        hashtags=primary.hashtags or fallback.hashtags,
        thumbnail_url=primary.thumbnail_url or fallback.thumbnail_url,
    )


def decide_outcome(metadata):
    if metadata is None:
        return "FAILED"
    if metadata.title and metadata.thumbnail_url:
        return "COMPLETED"
    if metadata.title or metadata.thumbnail_url:
        return "PARTIAL"
    return "FAILED"


def fetch_with_ytdlp(url):
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": YTDLP_TIMEOUT_SECONDS,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return Metadata()
    return from_ytdlp_info(info)


def fetch_with_oembed(url, platform, client=None):
    endpoint = _OEMBED_ENDPOINTS[platform]
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=OEMBED_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        response = client.get(endpoint, params={"url": url, "format": "json"})
        if response.status_code != 200 or len(response.content) > OEMBED_MAX_BYTES:
            return Metadata()
        return from_oembed(response.json())
    except (httpx.HTTPError, ValueError):
        return Metadata()
    finally:
        if own_client:
            client.close()


def fetch_metadata(url, platform):
    primary = fetch_with_ytdlp(url)
    if decide_outcome(primary) == "COMPLETED":
        return primary
    combined = merge(primary, fetch_with_oembed(url, platform))
    if decide_outcome(combined) == "FAILED":
        raise MetadataError("METADATA_UNAVAILABLE")
    return combined