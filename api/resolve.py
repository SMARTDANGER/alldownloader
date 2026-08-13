"""POST /api/resolve  {"url": "..."}  ->  media metadata + download options."""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    SECRET_REQUIRED_MESSAGE,
    detect_platform,
    extract,
    flat_entries,
    friendly_error,
    payload_for,
    secret_is_default,
    secret_misconfigured,
    valid_url,
)
import _spotify  # noqa: E402

# YouTube channel and handle pages, which are playlists in all but name.
YT_COLLECTION_RE = re.compile(r"/(?:channel/|user/|c/|@)[^/?#]+")


def looks_like_playlist(url: str, platform: str) -> bool:
    """Collections go through flat extraction. Getting this wrong is expensive:
    a channel page extracted in full would run well past the 60 s limit."""
    lowered = url.lower()
    if platform == "youtube":
        if "list=" in lowered and "watch?v=" not in lowered:
            return True
        return bool(YT_COLLECTION_RE.search(lowered))
    if platform == "soundcloud":
        return "/sets/" in lowered or lowered.rstrip("/").endswith(("/tracks", "/albums"))
    return False


def resolve(url: str) -> dict:
    platform = detect_platform(url)

    if platform == "spotify":
        return _spotify.resolve(url)

    if looks_like_playlist(url, platform):
        info = extract(url, flat=True)
        entries = flat_entries(info)
        if entries:
            return {
                "ok": True,
                "platform": platform,
                "type": "playlist",
                "title": info.get("title") or "Playlist",
                "count": len(entries),
                "entries": entries,
            }

    info = extract(url)
    if info.get("_type") == "playlist" or info.get("entries"):
        entries = flat_entries(info)
        first = (info.get("entries") or [None])[0]
        if len(entries) > 1:
            return {
                "ok": True,
                "platform": platform,
                "type": "playlist",
                "title": info.get("title") or "Playlist",
                "count": len(entries),
                "entries": entries,
            }
        if first:
            info = first
    return payload_for(info, platform, url)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, url: str):
        if secret_misconfigured():
            return self._send(503, {"ok": False, "error": SECRET_REQUIRED_MESSAGE})
        if not url or not valid_url(url):
            return self._send(400, {"ok": False, "error": "Paste a full http(s) link."})
        try:
            payload = resolve(url.strip())
        except Exception as exc:  # noqa: BLE001 — surfaced to the user as a message
            return self._send(422, {"ok": False, "error": friendly_error(exc)})
        payload.setdefault("insecure", secret_is_default())
        if not payload.get("entries") and not payload.get("options"):
            return self._send(
                422,
                {"ok": False, "error": "No downloadable stream was found for that link."},
            )
        self._send(200, payload)

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        self._handle((query.get("url") or [""])[0])

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            return self._send(400, {"ok": False, "error": "Invalid request body."})
        self._handle(str(body.get("url") or ""))
