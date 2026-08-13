"""Shared helpers for the serverless API: extraction, format shaping, signed tokens."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

DEV_SECRET = "insecure-dev-secret-set-DOWNLOAD_SECRET"
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "21600"))  # 6h
MAX_PLAYLIST_ENTRIES = 200

PLATFORMS = {
    "youtube": ("youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com"),
    "instagram": ("instagram.com", "instagr.am", "ddinstagram.com"),
    "tiktok": ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
    "soundcloud": ("soundcloud.com", "snd.sc", "on.soundcloud.com"),
    "spotify": ("spotify.com", "spotify.link"),
}


def secret() -> bytes:
    return (os.environ.get("DOWNLOAD_SECRET") or DEV_SECRET).encode()


def secret_is_default() -> bool:
    return not os.environ.get("DOWNLOAD_SECRET")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_token(payload: dict, ttl: int = TOKEN_TTL) -> str:
    """Sign a payload so /api/download will proxy it. Keeps the endpoint from
    being usable as an open proxy for arbitrary URLs."""
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    data = _b64(raw)
    sig = _b64(hmac.new(secret(), data.encode(), hashlib.sha256).digest())
    return f"{data}.{sig}"


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return "other"


def valid_url(url: str) -> bool:
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def ydl_opts(**extra) -> dict:
    """Base yt-dlp options. Cookies/proxy come from env because datacenter IPs
    (which is what Vercel runs on) get challenged by several of these sites."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": False,
        "no_color": True,
        "socket_timeout": 20,
        "retries": 2,
        "cachedir": False,
        # Vercel's filesystem is read-only apart from /tmp.
        "paths": {"home": "/tmp"},
    }
    proxy = os.environ.get("YTDLP_PROXY")
    if proxy:
        opts["proxy"] = proxy
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies:
        path = "/tmp/cookies.txt"
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(cookies.replace("\\n", "\n"))
        opts["cookiefile"] = path
    if os.environ.get("YTDLP_USER_AGENT"):
        opts.setdefault("http_headers", {})["User-Agent"] = os.environ["YTDLP_USER_AGENT"]
    opts.update(extra)
    return opts


def extract(url: str, flat: bool = False, **extra) -> dict:
    opts = ydl_opts(**extra)
    if flat:
        opts["extract_flat"] = "in_playlist"
        opts["noplaylist"] = False
    with YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def codec_label(vcodec: str) -> str:
    v = (vcodec or "").lower()
    if v in ("", "none"):
        return ""
    if v.startswith(("avc", "h264")):
        return "H.264"
    if v.startswith(("hev", "hvc", "h265")):
        return "HEVC"
    if v.startswith("av01"):
        return "AV1"
    if v.startswith(("vp9", "vp09")):
        return "VP9"
    if v.startswith(("vp8", "vp08")):
        return "VP8"
    return v.split(".")[0].upper()


def audio_codec_label(acodec: str) -> str:
    a = (acodec or "").lower()
    if a in ("", "none"):
        return ""
    if a.startswith("mp4a"):
        return "AAC"
    if a.startswith("opus"):
        return "Opus"
    if a.startswith("mp3"):
        return "MP3"
    if a.startswith("vorbis"):
        return "Vorbis"
    if a.startswith(("flac", "alac")):
        return a[:4].upper()
    return a.split(".")[0].upper()


def _size(fmt: dict):
    return fmt.get("filesize") or fmt.get("filesize_approx")


def _quality_name(height, fps) -> str:
    if not height:
        return "Video"
    names = {4320: "8K", 2880: "5K", 2160: "4K", 1440: "1440p", 1080: "1080p"}
    base = names.get(height) or f"{height}p"
    if fps and fps >= 50:
        base += f"{int(fps)}"
    return base


def _is_direct(fmt: dict) -> bool:
    """Only progressive http(s) formats can be streamed straight to the browser
    as a single file. HLS/DASH manifests would need remuxing."""
    proto = (fmt.get("protocol") or "").lower()
    return proto in ("https", "http") and bool(fmt.get("url"))


def shape_formats(info: dict) -> dict:
    """Turn yt-dlp's raw format list into the small, sorted lists the UI uses."""
    raw = info.get("formats") or []
    if not raw and info.get("url"):
        raw = [info]

    videos, audios = [], []
    for fmt in raw:
        if not _is_direct(fmt):
            continue
        vcodec, acodec = fmt.get("vcodec"), fmt.get("acodec")
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"
        if not has_video and not has_audio:
            continue
        entry = {
            "format_id": str(fmt.get("format_id") or ""),
            "ext": fmt.get("ext") or "",
            "filesize": _size(fmt),
            "tbr": fmt.get("tbr") or 0,
            "url": fmt.get("url"),
            "headers": fmt.get("http_headers") or {},
        }
        if has_video:
            entry.update(
                {
                    "kind": "video",
                    "height": fmt.get("height") or 0,
                    "width": fmt.get("width") or 0,
                    "fps": fmt.get("fps") or 0,
                    "vcodec": codec_label(vcodec),
                    "acodec": audio_codec_label(acodec) if has_audio else "",
                    "muxed": bool(has_audio),
                    "dynamic_range": fmt.get("dynamic_range") or "",
                }
            )
            entry["label"] = _quality_name(entry["height"], entry["fps"])
            videos.append(entry)
        else:
            entry.update(
                {
                    "kind": "audio",
                    "abr": round(fmt.get("abr") or fmt.get("tbr") or 0),
                    "acodec": audio_codec_label(acodec),
                    "asr": fmt.get("asr") or 0,
                }
            )
            entry["label"] = f"{entry['abr']} kbps" if entry["abr"] else "Audio"
            audios.append(entry)

    videos.sort(key=lambda f: (f["height"], f["fps"], f["tbr"]), reverse=True)
    audios.sort(key=lambda f: (f["abr"], f["tbr"]), reverse=True)
    return {"videos": _dedupe_videos(videos), "audios": _dedupe_audios(audios)}


def _dedupe_videos(videos):
    seen, out = {}, []
    for fmt in videos:
        key = (fmt["height"], fmt["fps"], fmt["vcodec"], fmt["muxed"], fmt["ext"])
        if key not in seen:
            seen[key] = True
            out.append(fmt)
    return out


def _dedupe_audios(audios):
    seen, out = set(), []
    for fmt in audios:
        key = (fmt["abr"], fmt["acodec"], fmt["ext"])
        if key not in seen:
            seen.add(key)
            out.append(fmt)
    return out


def best_audio_for(video: dict, audios: list):
    """Pick the audio track that pairs cleanly with a video-only stream:
    same container family first so a copy-mux stays valid."""
    if not audios:
        return None
    family = "mp4" if video.get("ext") in ("mp4", "m4v", "mov") else "webm"
    preferred = [a for a in audios if (a["ext"] in ("m4a", "mp4")) == (family == "mp4")]
    return (preferred or audios)[0]


def safe_filename(name: str, ext: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", name or "download").strip()
    name = re.sub(r"\s+", " ", name)[:120].strip(". ") or "download"
    return f"{name}.{ext or 'bin'}"


def build_option(title: str, fmt: dict, audio: dict | None, mux_url: str | None) -> dict:
    """Build one downloadable choice, including its signed token."""
    is_video = fmt["kind"] == "video"
    needs_mux = is_video and not fmt.get("muxed") and audio is not None

    if needs_mux:
        container = "mp4" if fmt["ext"] in ("mp4", "m4v") and audio["ext"] in ("m4a", "mp4") else "mkv"
    else:
        container = fmt["ext"]

    option = {
        "id": fmt["format_id"] + ("+" + audio["format_id"] if needs_mux else ""),
        "kind": fmt["kind"],
        "label": fmt["label"],
        "ext": container,
        "filesize": fmt.get("filesize"),
        "codec": fmt.get("vcodec") if is_video else fmt.get("acodec"),
        "audio_codec": (audio or fmt).get("acodec") if is_video else None,
        "height": fmt.get("height") if is_video else None,
        "fps": fmt.get("fps") if is_video else None,
        "hdr": (fmt.get("dynamic_range") or "").upper() not in ("", "SDR"),
        "muxed": bool(fmt.get("muxed")) or needs_mux,
        "needs_mux": needs_mux,
        "mux_available": bool(mux_url) if needs_mux else False,
    }
    if needs_mux:
        option["filesize"] = (fmt.get("filesize") or 0) + (audio.get("filesize") or 0) or None

    filename = safe_filename(title, container)
    if needs_mux and mux_url:
        option["url"] = mux_url.rstrip("/") + "/mux?t=" + sign_token(
            {
                "v": fmt["url"],
                "a": audio["url"],
                "vh": fmt["headers"],
                "ah": audio["headers"],
                "c": container,
                "n": filename,
            }
        )
    elif needs_mux:
        # No mux worker configured: hand over the two streams separately.
        option["parts"] = [
            {
                "label": f"Video ({fmt['label']}, no sound)",
                "url": download_url(fmt, safe_filename(title + " [video]", fmt["ext"])),
                "filesize": fmt.get("filesize"),
            },
            {
                "label": f"Audio ({audio['label']})",
                "url": download_url(audio, safe_filename(title + " [audio]", audio["ext"])),
                "filesize": audio.get("filesize"),
            },
        ]
    else:
        option["url"] = download_url(fmt, filename)
    return option


def download_url(fmt: dict, filename: str) -> str:
    return "/api/download?t=" + sign_token(
        {"u": fmt["url"], "h": fmt["headers"], "n": filename}
    )


def quick_picks(options: list) -> list:
    """The three big buttons: best available, most compatible, best audio."""
    picks = []
    videos = [o for o in options if o["kind"] == "video"]
    audios = [o for o in options if o["kind"] == "audio"]

    if videos:
        best = max(videos, key=lambda o: (o.get("height") or 0, o.get("fps") or 0))
        picks.append(dict(best, pick="Best quality"))

        # "Compatible" has to arrive as one ready-to-play file, so video-only
        # streams only qualify when a merge worker can join them.
        single_file = [o for o in videos if not o["needs_mux"] or o["mux_available"]]
        compatible = [
            o for o in single_file if o.get("codec") == "H.264" and o["ext"] in ("mp4", "m4v")
        ] or single_file
        if compatible:
            top = max(compatible, key=lambda o: (o.get("height") or 0, o.get("fps") or 0))
            if top["id"] != best["id"]:
                picks.append(dict(top, pick="Most compatible"))
    if audios:
        picks.append(dict(audios[0], pick="Audio only"))
    return picks


def payload_for(info: dict, platform: str, source_url: str, title: str | None = None) -> dict:
    mux_url = os.environ.get("MUX_URL")
    shaped = shape_formats(info)
    videos, audios = shaped["videos"], shaped["audios"]
    title = title or info.get("title") or "download"

    options = []
    for fmt in videos:
        audio = None if fmt["muxed"] else best_audio_for(fmt, audios)
        if not fmt["muxed"] and audio is None:
            continue  # video-only with no audio to pair — not useful
        options.append(build_option(title, fmt, audio, mux_url))
    for fmt in audios:
        options.append(build_option(title, fmt, None, mux_url))

    return {
        "ok": True,
        "platform": platform,
        "url": source_url,
        "title": title,
        "uploader": info.get("uploader") or info.get("channel") or info.get("artist") or "",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "options": options,
        "quick": quick_picks(options),
        "insecure": secret_is_default(),
    }


def flat_entries(info: dict) -> list:
    entries = []
    for entry in (info.get("entries") or [])[:MAX_PLAYLIST_ENTRIES]:
        if not entry:
            continue
        url = entry.get("url") or entry.get("webpage_url")
        if not url:
            continue
        entries.append(
            {
                "title": entry.get("title") or "Untitled",
                "url": url,
                "uploader": entry.get("uploader") or entry.get("channel") or "",
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail"),
            }
        )
    return entries


FRIENDLY_ERRORS = (
    (r"sign in to confirm|not a bot|cookies", "This site is challenging the server. Add YTDLP_COOKIES (and optionally YTDLP_PROXY) in your Vercel environment variables."),
    (r"ip address is blocked|blocked from accessing", "The site blocked this server's IP address. Set YTDLP_PROXY to a residential proxy in your Vercel environment variables."),
    (r"drm", "That track is DRM-protected and cannot be downloaded."),
    (r"private|login required|requested content is not available", "That post is private or needs a login. Add YTDLP_COOKIES to access it."),
    (r"unsupported url", "That link isn't supported."),
    (r"video unavailable|removed|deleted", "The media is unavailable or has been removed."),
    (r"geo|country", "This media is geo-blocked from the server's region. Try setting YTDLP_PROXY."),
    (r"rate.?limit|429|too many requests", "Rate limited by the site. Wait a moment and try again."),
)


def friendly_error(exc: Exception) -> str:
    message = str(exc)
    low = message.lower()
    for pattern, friendly in FRIENDLY_ERRORS:
        if re.search(pattern, low):
            return friendly
    message = re.sub(r"^ERROR:\s*", "", message).strip()
    return (message[:280] or "Could not read that link.")
