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


def secret_misconfigured() -> bool:
    """True when this deployment must refuse to sign links.

    The fallback secret is published in this repository, so serving with it
    would make the download endpoint a forgeable proxy for any URL. Local work
    is allowed to use it; anything running on Vercel is not.
    """
    on_vercel = bool(os.environ.get("VERCEL"))
    is_local_dev = os.environ.get("VERCEL_ENV") == "development"
    return secret_is_default() and on_vercel and not is_local_dev


SECRET_REQUIRED_MESSAGE = (
    "This deployment has no DOWNLOAD_SECRET set, so it will not issue download "
    "links. Add DOWNLOAD_SECRET (any long random string) in your Vercel project's "
    "Environment Variables and redeploy."
)


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
            # Write then rename: concurrent invocations share one instance under
            # Fluid compute, and a reader must never see a half-written jar.
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(cookies.replace("\\n", "\n"))
            os.replace(tmp, path)
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
        # Stop yt-dlp walking a 5000-video channel we are only going to
        # truncate anyway.
        opts["playlistend"] = MAX_PLAYLIST_ENTRIES
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


AUDIO_EXTS = {"mp3", "m4a", "aac", "opus", "ogg", "oga", "wav", "flac", "weba", "aiff", "alac"}
VIDEO_EXTS = {"mp4", "m4v", "mov", "webm", "mkv", "avi", "flv", "3gp", "ts"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "bmp", "svg"}


def classify(fmt: dict) -> tuple[bool, bool]:
    """Work out whether a format carries video and/or audio.

    Only 'none' means a stream is definitively absent. A *missing* codec means
    the extractor didn't report one, which is not the same thing — Instagram,
    for instance, omits acodec entirely on its progressive MP4s and sets it to
    'none' only when the clip genuinely has no sound. Reading missing as absent
    threw those complete files away and left the silent DASH video track.

    Evidence is weighed in order: an explicit 'none', then a reported codec or
    dimensions, and only then the container extension. Container last matters —
    an audio-only stream delivered in an .mp4 must not read as a video.
    """
    ext = (fmt.get("ext") or "").lower()
    if ext in IMAGE_EXTS:
        return False, False  # photo posts and storyboards are not downloads we offer

    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()
    video_absent, audio_absent = vcodec == "none", acodec == "none"
    if video_absent and audio_absent:
        return False, False

    vcodec_known = vcodec not in ("", "none", "unknown")
    acodec_known = acodec not in ("", "none", "unknown")
    has_dimensions = bool(fmt.get("height") or fmt.get("width"))
    audio_hints = bool(fmt.get("abr") or fmt.get("asr")) or ext in AUDIO_EXTS

    if video_absent:
        has_video = False
    elif vcodec_known or has_dimensions:
        has_video = True
    else:
        # Nothing states there is a picture. Trust the container only when
        # nothing points at audio instead.
        has_video = ext in VIDEO_EXTS and not (acodec_known or audio_hints)

    if has_video:
        # A progressive video container with no audio metadata is assumed to
        # carry sound; that is what it means for a site not to report codecs.
        has_audio = not audio_absent and (acodec_known or ext in VIDEO_EXTS)
    else:
        # vcodec 'none' is itself proof this is a standalone audio stream, even
        # when the extractor names neither the codec nor a familiar extension
        # (SoundCloud's lossless "Original file" download, for one).
        has_audio = not audio_absent and (acodec_known or audio_hints or video_absent)
    return has_video, has_audio


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
        has_video, has_audio = classify(fmt)
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
            height, width = fmt.get("height") or 0, fmt.get("width") or 0
            entry.update(
                {
                    "kind": "video",
                    "height": height,
                    "width": width,
                    # Portrait clips (reels, Shorts, TikToks) are 1080x1920 —
                    # people call that 1080p, so rank and label by the short side.
                    "quality": min(width, height) if width and height else height,
                    "fps": fmt.get("fps") or 0,
                    "vcodec": codec_label(vcodec),
                    "acodec": audio_codec_label(acodec) if has_audio else "",
                    "muxed": bool(has_audio),
                    "dynamic_range": fmt.get("dynamic_range") or "",
                }
            )
            entry["label"] = _quality_name(entry["quality"], entry["fps"])
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

    # Ranked by quality, then by whether the file already carries its own audio —
    # at equal quality a ready-to-play file beats one that needs merging.
    videos.sort(key=lambda f: (f["quality"], f["fps"], f["muxed"], f["tbr"]), reverse=True)
    audios.sort(key=lambda f: (f["abr"], f["tbr"]), reverse=True)
    return {"videos": _dedupe_videos(videos), "audios": _dedupe_audios(audios)}


def _dedupe_videos(videos):
    seen, out = {}, []
    for fmt in videos:
        key = (fmt["quality"], fmt["fps"], fmt["vcodec"], fmt["muxed"], fmt["ext"])
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
        # The "p" number a viewer would recognise — short side, so a portrait
        # 1080x1920 reel reads as 1080p rather than 1920p.
        "height": fmt.get("quality") if is_video else None,
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
        # At equal quality prefer the option that needs no merging, so "Best
        # quality" only turns into a two-file download when it genuinely buys
        # more resolution.
        best = max(
            videos,
            key=lambda o: (
                o.get("height") or 0,
                o.get("fps") or 0,
                not (o["needs_mux"] and not o["mux_available"]),
            ),
        )
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
        # A video-only stream with nothing to pair it with is still worth
        # offering — some clips genuinely have no sound — but it downloads as
        # a plain silent file rather than a merge.
        audio = None if fmt["muxed"] else best_audio_for(fmt, audios)
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
        # webpage_url first: on a fully-extracted entry (an Instagram carousel,
        # say) "url" is the direct CDN media link, and posting that back to
        # /api/resolve would hand it to the generic extractor.
        url = entry.get("webpage_url") or entry.get("url")
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
