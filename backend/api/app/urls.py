import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 3

YOUTUBE = "YOUTUBE"
TIKTOK = "TIKTOK"

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_YT_SHORT_HOST = "youtu.be"
_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_TT_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}

_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_SHORTS_PATH = re.compile(r"/shorts/([A-Za-z0-9_-]{11})/?")
_YT_SHORT_LINK_PATH = re.compile(r"/([A-Za-z0-9_-]{11})/?")
_TT_VIDEO_PATH = re.compile(r"/@([A-Za-z0-9._]{1,64})/video/(\d{8,25})/?")
_TT_T_PATH = re.compile(r"/t/([A-Za-z0-9]{4,32})/?")
_TT_SHORT_PATH = re.compile(r"/([A-Za-z0-9]{4,32})/?")

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UrlRejected(Exception):

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ValidatedUrl:
    platform: str  
    canonical_url: str  
    needs_resolution: bool = False  


def validate_url(raw: str) -> ValidatedUrl:
    raw = (raw or "").strip()
    if not raw or len(raw) > MAX_URL_LENGTH:
        raise UrlRejected("INVALID_URL")
    if any(ord(c) < 32 or c.isspace() for c in raw):
        raise UrlRejected("INVALID_URL")
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise UrlRejected("INVALID_URL")
    if parts.scheme.lower() != "https":
        raise UrlRejected("NOT_HTTPS")
    if parts.username is not None or parts.password is not None:
        raise UrlRejected("CREDENTIALS_IN_URL")
    if not host:
        raise UrlRejected("INVALID_URL")
    if port not in (None, 443):
        raise UrlRejected("UNSUPPORTED_HOST")

    if host in _YT_HOSTS:
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0]
            if _YT_ID.fullmatch(video_id):
                return ValidatedUrl(YOUTUBE, f"https://www.youtube.com/watch?v={video_id}")
        m = _YT_SHORTS_PATH.fullmatch(parts.path)
        if m:
            return ValidatedUrl(YOUTUBE, f"https://www.youtube.com/shorts/{m.group(1)}")
        raise UrlRejected("UNSUPPORTED_PATH")

    if host == _YT_SHORT_HOST:
        m = _YT_SHORT_LINK_PATH.fullmatch(parts.path)
        if m:
            return ValidatedUrl(YOUTUBE, f"https://www.youtube.com/watch?v={m.group(1)}")
        raise UrlRejected("UNSUPPORTED_PATH")

    if host in _TT_HOSTS:
        m = _TT_VIDEO_PATH.fullmatch(parts.path)
        if m:
            return ValidatedUrl(
                TIKTOK, f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
            )
        m = _TT_T_PATH.fullmatch(parts.path)
        if m:
            return ValidatedUrl(
                TIKTOK, f"https://www.tiktok.com/t/{m.group(1)}/", needs_resolution=True
            )
        raise UrlRejected("UNSUPPORTED_PATH")

    if host in _TT_SHORT_HOSTS:
        m = _TT_SHORT_PATH.fullmatch(parts.path)
        if m:
            return ValidatedUrl(
                TIKTOK, f"https://{host}/{m.group(1)}/", needs_resolution=True
            )
        raise UrlRejected("UNSUPPORTED_PATH")

    raise UrlRejected("UNSUPPORTED_HOST")


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    try:
        return bool(infos) and all(
            ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in infos
        )
    except ValueError:
        return False


def resolve_short_link(
    url: str, client=None, host_check=_host_is_public
) -> ValidatedUrl:
    own_client = client is None
    if own_client:
        client = httpx.Client(
            follow_redirects=False,
            timeout=5.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SVP-research/1.0)"},
        )
    try:
        current = url
        for hop_number in range(MAX_REDIRECTS + 1):
            try:
                hop = validate_url(current)
            except UrlRejected:
                if hop_number == 0:
                    raise
                raise UrlRejected("REDIRECT_BLOCKED")
            if not hop.needs_resolution:
                return hop
            host = urlsplit(hop.canonical_url).hostname
            if not host_check(host):
                raise UrlRejected("HOST_BLOCKED")
            with client.stream("GET", hop.canonical_url) as response:
                status = response.status_code
                location = response.headers.get("location")
            if status not in _REDIRECT_STATUSES or not location:
                raise UrlRejected("SHORT_LINK_UNRESOLVED")
            current = str(httpx.URL(hop.canonical_url).join(location))
        raise UrlRejected("REDIRECT_TOO_MANY")
    except httpx.HTTPError:
        raise UrlRejected("SHORT_LINK_UNRESOLVED")
    finally:
        if own_client:
            client.close()