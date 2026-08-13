"""Spotify support.

Spotify's own audio streams are DRM-protected and are not touched here. What
this does is read the *metadata* of a track/album/playlist (title, artist,
duration) and then find the same recording on YouTube/YouTube Music, which is
the approach open-source tools like spotDL use. Nothing is decrypted.

Full metadata needs Spotify app credentials (client-credentials flow, free):
set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET. Without them, single tracks
still work through the public oEmbed endpoint, but albums/playlists cannot be
enumerated.
"""

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

from _common import extract, payload_for

_TOKEN = {"value": None, "expires": 0}
SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)/([A-Za-z0-9]+)"
)


def parse_spotify_url(url: str):
    match = SPOTIFY_URL_RE.search(url)
    return (match.group(1), match.group(2)) if match else (None, None)


def has_credentials() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def _get_json(url: str, headers: dict | None = None, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def _token() -> str:
    if _TOKEN["value"] and _TOKEN["expires"] > time.time() + 30:
        return _TOKEN["value"]
    creds = f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}"
    auth = base64.b64encode(creds.encode()).decode()
    body = _get_json(
        "https://accounts.spotify.com/api/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=b"grant_type=client_credentials",
    )
    _TOKEN["value"] = body["access_token"]
    _TOKEN["expires"] = time.time() + body.get("expires_in", 3600)
    return _TOKEN["value"]


def _api(path: str) -> dict:
    return _get_json(
        "https://api.spotify.com/v1/" + path.lstrip("/"),
        headers={"Authorization": "Bearer " + _token()},
    )


def _track_meta(track: dict) -> dict:
    return {
        "title": track.get("name") or "Untitled",
        "artists": ", ".join(a["name"] for a in track.get("artists") or []),
        "duration": round((track.get("duration_ms") or 0) / 1000) or None,
        "thumbnail": ((track.get("album") or {}).get("images") or [{}])[0].get("url"),
        "url": f"https://open.spotify.com/track/{track.get('id')}",
    }


def track_metadata(track_id: str) -> dict:
    if has_credentials():
        return _track_meta(_api(f"tracks/{track_id}"))

    # oEmbed fallback: title only, no reliable artist split.
    share = f"https://open.spotify.com/track/{track_id}"
    body = _get_json(
        "https://open.spotify.com/oembed?url=" + urllib.parse.quote(share, safe=""),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    title = body.get("title") or "Untitled"
    artists = ""
    if " - " in title:  # oEmbed often returns "Artist - Track"
        artists, title = (part.strip() for part in title.split(" - ", 1))
    return {
        "title": title,
        "artists": artists,
        "duration": None,
        "thumbnail": body.get("thumbnail_url"),
        "url": share,
    }


def collection_entries(kind: str, spotify_id: str) -> tuple[str, list]:
    if not has_credentials():
        raise RuntimeError(
            "Spotify albums and playlists need app credentials. Add SPOTIFY_CLIENT_ID "
            "and SPOTIFY_CLIENT_SECRET to your Vercel environment variables."
        )
    entries, name = [], "Spotify"
    if kind == "album":
        album = _api(f"albums/{spotify_id}")
        name = album.get("name") or name
        cover = ((album.get("images") or [{}])[0]).get("url")
        items = (album.get("tracks") or {}).get("items") or []
        for track in items:
            meta = _track_meta({**track, "album": {"images": [{"url": cover}] if cover else []}})
            entries.append(_as_entry(meta))
    else:
        playlist = _api(f"playlists/{spotify_id}?fields=name,tracks.items(track(id,name,artists,duration_ms,album(images))),tracks.next")
        name = playlist.get("name") or name
        for item in (playlist.get("tracks") or {}).get("items") or []:
            track = item.get("track")
            if track and track.get("id"):
                entries.append(_as_entry(_track_meta(track)))
    return name, entries


def _as_entry(meta: dict) -> dict:
    return {
        "title": meta["title"],
        "url": meta["url"],
        "uploader": meta["artists"],
        "duration": meta["duration"],
        "thumbnail": meta["thumbnail"],
    }


def _score(candidate: dict, target_duration: int | None, query_terms: list) -> tuple:
    duration = candidate.get("duration") or 0
    gap = abs(duration - target_duration) if target_duration else 0
    title = (candidate.get("title") or "").lower()
    # Prefer official audio uploads over live/cover versions.
    penalty = sum(4 for word in ("live", "cover", "remix", "sped up", "reaction") if word in title)
    bonus = sum(-2 for term in query_terms if term and term in title)
    return (gap + penalty + bonus,)


def find_audio(meta: dict) -> dict:
    """Search YouTube for the recording described by the Spotify metadata."""
    query = " ".join(filter(None, [meta.get("artists"), meta.get("title")])) or meta["title"]
    # Flat search first — extracting all five candidates in full would blow the
    # function's time budget.
    results = extract(f"ytsearch5:{query} audio", flat=True)
    candidates = [entry for entry in (results.get("entries") or []) if entry]
    if not candidates:
        raise RuntimeError(f"No audio source found for “{query}”.")

    terms = [term.lower() for term in re.split(r"\W+", query) if len(term) > 2]
    best = min(candidates, key=lambda entry: _score(entry, meta.get("duration"), terms))
    if not best.get("formats"):  # flat result — fetch the real thing
        best = extract(best.get("webpage_url") or best["url"])
    return best


def resolve(url: str) -> dict:
    kind, spotify_id = parse_spotify_url(url)
    if not kind:
        raise RuntimeError("That doesn't look like a Spotify track, album or playlist link.")

    if kind in ("album", "playlist"):
        name, entries = collection_entries(kind, spotify_id)
        return {
            "ok": True,
            "platform": "spotify",
            "type": "playlist",
            "title": name,
            "count": len(entries),
            "entries": entries,
        }

    meta = track_metadata(spotify_id)
    info = find_audio(meta)
    label = " - ".join(filter(None, [meta.get("artists"), meta["title"]]))
    payload = payload_for(info, "spotify", url, title=label)
    payload["title"] = meta["title"]
    payload["uploader"] = meta["artists"] or payload["uploader"]
    payload["thumbnail"] = meta["thumbnail"] or payload["thumbnail"]
    payload["duration"] = meta["duration"] or payload["duration"]
    payload["options"] = [option for option in payload["options"] if option["kind"] == "audio"]
    payload["quick"] = [pick for pick in payload["quick"] if pick["kind"] == "audio"]
    payload["matched_from"] = info.get("webpage_url") or ""
    payload["note"] = "Matched to the closest audio source — Spotify's own streams are DRM-protected."
    return payload
