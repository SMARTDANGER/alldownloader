"""Format classification and option-building tests.

No network and no dependencies beyond yt-dlp's absence — the fixtures are
hand-built to match what each extractor really emits. Run with:

    python3 tests/test_formats.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
os.environ["DOWNLOAD_SECRET"] = "test-secret"
os.environ.pop("MUX_URL", None)
os.environ.pop("VERCEL", None)

import _common as C  # noqa: E402

FAILURES = []
UA = {"User-Agent": "Mozilla/5.0"}


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILURES.append(name)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# --------------------------------------------------------------------------
section("classify(): only an explicit 'none' proves a stream is absent")

CLASSIFY_CASES = [
    # Instagram: has_audio true means the acodec key is simply absent.
    ("instagram progressive with sound",
     {"ext": "mp4", "url": "u", "protocol": "https", "width": 1080, "height": 1920}, (True, True)),
    ("instagram progressive, genuinely silent",
     {"ext": "mp4", "url": "u", "protocol": "https", "width": 1080, "height": 1920,
      "acodec": "none"}, (True, False)),
    # Container must not outrank a reported audio codec.
    ("audio-only stream in an mp4 container",
     {"ext": "mp4", "url": "u", "protocol": "https", "acodec": "mp4a.40.2", "abr": 128}, (False, True)),
    ("audio-only stream in a webm container",
     {"ext": "webm", "url": "u", "protocol": "https", "acodec": "opus", "abr": 160}, (False, True)),
    # vcodec 'none' is proof of an audio stream even with an unfamiliar ext.
    ("soundcloud lossless original (aiff, no acodec)",
     {"ext": "aiff", "url": "u", "protocol": "https", "vcodec": "none"}, (False, True)),
    # Images are never offered.
    ("photo post with dimensions",
     {"ext": "jpg", "url": "u", "protocol": "https", "width": 1080, "height": 1080}, (False, False)),
    ("explicitly empty format",
     {"ext": "mp4", "url": "u", "protocol": "https", "vcodec": "none", "acodec": "none"}, (False, False)),
    # Sites that report codecs properly are untouched.
    ("youtube 8K video-only",
     {"ext": "mp4", "url": "u", "protocol": "https", "vcodec": "av01.0.16M.10", "acodec": "none",
      "height": 4320}, (True, False)),
    ("youtube m4a audio-only",
     {"ext": "m4a", "url": "u", "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.2",
      "abr": 129}, (False, True)),
    # Zero metadata: fall back to the container.
    ("bare mp4 with no metadata at all",
     {"ext": "mp4", "url": "u", "protocol": "https"}, (True, True)),
    ("bare mp3 with no metadata at all",
     {"ext": "mp3", "url": "u", "protocol": "https"}, (False, True)),
]
for name, fmt, want in CLASSIFY_CASES:
    check(name, C.classify(fmt), want)


# --------------------------------------------------------------------------
def progressive(fid, w, h, size, **extra):
    """An Instagram video_versions entry: no codec keys unless sound is absent."""
    return {"format_id": fid, "url": f"https://scontent.cdninstagram.com/{fid}.mp4",
            "width": w, "height": h, "ext": "mp4", "protocol": "https",
            "filesize": size, "http_headers": UA, **extra}


def dash(fid, **kw):
    """An unfragmented DASH representation: explicit codecs, protocol stays https."""
    return {"format_id": fid, "url": f"https://scontent.cdninstagram.com/{fid}.mp4",
            "ext": "mp4", "protocol": "https", "http_headers": UA, **kw}


section("instagram reel arrives as one file with sound")
reel = {"title": "Reel", "duration": 15, "formats": [
    progressive("101", 1080, 1920, 8_400_000),
    progressive("102", 640, 1136, 2_100_000),
    dash("dash-0", width=1080, height=1920, vcodec="avc1.4d401f", acodec="none",
         filesize=9_000_000, tbr=4800),
    dash("dash-1", vcodec="none", acodec="mp4a.40.2", abr=64, filesize=120_000, tbr=64),
]}
best = C.payload_for(reel, "instagram", "https://www.instagram.com/reel/abc/")["quick"][0]
check("best pick is the progressive stream", best["id"], "101")
check("best pick carries audio", (best["muxed"], best["needs_mux"]), (True, False))
check("best pick is a single download", "parts" in best, False)
check("portrait clip labelled by its short side", best["label"], "1080p")

section("a clip with no audio anywhere is still downloadable")
silent = {"title": "Silent", "formats": [progressive("301", 1080, 1920, 3_000_000, acodec="none")]}
options = C.payload_for(silent, "instagram", "https://www.instagram.com/reel/q/")["options"]
check("offered rather than dropped", len(options), 1)
check("offered as a plain single file", (options[0]["needs_mux"], "parts" in options[0]), (False, False))
check("flagged as having no sound", options[0]["muxed"], False)

section("image-only post yields nothing to download")
photo = {"title": "Photo", "formats": [
    {"format_id": "img", "url": "https://x/p.jpg", "ext": "jpg", "protocol": "https",
     "width": 1080, "height": 1080}]}
check("no options", C.payload_for(photo, "instagram", "https://x/p/")["options"], [])

section("youtube behaviour is unchanged")
yt = {"title": "YT", "formats": [
    {"format_id": "702", "ext": "mp4", "vcodec": "av01.0.16M.10", "acodec": "none", "height": 4320,
     "width": 7680, "fps": 60, "protocol": "https", "url": "https://v/8k", "tbr": 60000,
     "filesize": 9_000_000_000, "http_headers": {}, "dynamic_range": "HDR10"},
    {"format_id": "22", "ext": "mp4", "vcodec": "avc1.64001F", "acodec": "mp4a.40.2", "height": 720,
     "width": 1280, "fps": 30, "protocol": "https", "url": "https://v/720", "tbr": 1500,
     "filesize": 50_000_000, "http_headers": {}},
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129,
     "protocol": "https", "url": "https://a/m4a", "tbr": 129, "filesize": 4_000_000,
     "http_headers": {}},
    {"format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 1080,
     "protocol": "m3u8_native", "url": "https://v/hls"},
]}
p = C.payload_for(yt, "youtube", "https://youtu.be/x")
picks = {q["pick"]: q for q in p["quick"]}
check("8K is the best pick", picks["Best quality"]["label"], "8K60")
check("8K still needs merging", picks["Best quality"]["needs_mux"], True)
check("8K pairs with the mp4-family audio", picks["Best quality"]["id"], "702+140")
check("HDR detected", picks["Best quality"]["hdr"], True)
check("most compatible is a single file", picks["Most compatible"]["needs_mux"], False)
check("HLS filtered out", any("hls" in o["id"] for o in p["options"]), False)

section("equal quality prefers the ready-to-play file")
tik = {"title": "TikTok", "formats": [
    dash("h265", width=1080, height=1920, vcodec="hev1.1.6.L120", acodec="none",
         filesize=6_000_000, tbr=3000),
    dash("h264", width=1080, height=1920, vcodec="avc1.64001f", acodec="mp4a.40.2",
         filesize=5_000_000, tbr=2500),
    dash("audio", vcodec="none", acodec="mp4a.40.2", abr=128, filesize=200_000, tbr=128),
]}
best = C.payload_for(tik, "tiktok", "https://www.tiktok.com/@a/video/1")["quick"][0]
check("tie goes to the single file", (best["needs_mux"], best["codec"]), (False, "H.264"))

section("playlist entries point at pages, not CDN media")
info = {"entries": [{"title": "Item", "url": "https://cdn.example/media.mp4?sig=abc",
                     "webpage_url": "https://www.instagram.com/p/XYZ/"}]}
check("webpage_url wins", C.flat_entries(info)[0]["url"], "https://www.instagram.com/p/XYZ/")
info = {"entries": [{"title": "Item", "url": "https://www.youtube.com/watch?v=abc"}]}
check("falls back to url when that is the page",
      C.flat_entries(info)[0]["url"], "https://www.youtube.com/watch?v=abc")

section("filenames are safe for the filesystem")
check("path separators and reserved chars stripped",
      C.safe_filename('a/b:c*d?"e<f>g|h', "mp4"), "abcdefgh.mp4")
check("empty title still yields a name", C.safe_filename("", "mp3"), "download.mp3")
check("trailing dots trimmed", C.safe_filename("name...", "mp4"), "name.mp4")

section("signed links")
token = C.sign_token({"u": "https://x/y", "h": {}, "n": "a.mp4"}).split(".")
check("token has payload and signature", len(token), 2)

# --------------------------------------------------------------------------
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print(f"all {sum(1 for _ in CLASSIFY_CASES) + 22} checks passed")
