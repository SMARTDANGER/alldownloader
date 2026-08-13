"""Optional merge worker.

Vercel's serverless functions cannot ship ffmpeg and stream a multi-gigabyte
remux inside the execution limit, so the merge step lives here instead. Deploy
this container anywhere that allows long-running responses (Fly.io, Railway,
Render, a VPS), then point the site at it:

    MUX_URL=https://your-worker.example.com
    DOWNLOAD_SECRET=<the same secret as the Vercel project>

With it configured, 8K/HDR/AV1 YouTube downloads arrive as one finished file
instead of separate video and audio streams. Without it the site still works —
it just hands over the two streams.

The merge is a stream copy (`-c copy`): no re-encoding, so it is fast and
lossless, and quality is exactly what the source provides.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

app = FastAPI(title="AllDownloader mux worker")

SECRET = (os.environ.get("DOWNLOAD_SECRET") or "insecure-dev-secret-set-DOWNLOAD_SECRET").encode()
CHUNK = 256 * 1024


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify(token: str) -> dict:
    data, _, signature = token.rpartition(".")
    if not data:
        raise HTTPException(403, "Malformed token")
    expected = hmac.new(SECRET, data.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(signature)):
        raise HTTPException(403, "Invalid token")
    payload = json.loads(_unb64(data))
    if payload.get("exp", 0) < time.time():
        raise HTTPException(410, "Link expired")
    if not payload.get("v") or not payload.get("a"):
        raise HTTPException(400, "Token is not a merge request")
    return payload


def header_args(headers: dict) -> list:
    """ffmpeg takes per-input HTTP headers as one CRLF-joined blob."""
    if not headers:
        return []
    blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    return ["-headers", blob]


def build_command(payload: dict) -> list:
    container = "mp4" if payload.get("c") == "mp4" else "matroska"
    command = ["ffmpeg", "-loglevel", "error", "-nostdin"]
    command += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    command += header_args(payload.get("vh")) + ["-i", payload["v"]]
    command += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    command += header_args(payload.get("ah")) + ["-i", payload["a"]]
    command += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest"]
    if container == "mp4":
        # Fragmented MP4 so the file is valid while still being written to a pipe.
        command += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    command += ["-f", container, "pipe:1"]
    return command


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/mux")
async def mux(t: str = Query(..., description="Signed token from /api/resolve")):
    payload = verify(t)
    filename = payload.get("n") or "download.mp4"

    process = await asyncio.create_subprocess_exec(
        *build_command(payload),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def stream():
        try:
            while True:
                chunk = await process.stdout.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return StreamingResponse(
        stream(),
        media_type="video/mp4" if payload.get("c") == "mp4" else "video/x-matroska",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
            ),
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
