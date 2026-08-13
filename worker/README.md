# Merge worker (optional)

Joins a video-only stream and an audio-only stream into one file, so 8K/4K/HDR YouTube
downloads arrive as a single ready-to-play file instead of two parts.

It exists as a separate service because Vercel functions cannot ship ffmpeg or hold a
multi-gigabyte remux open long enough. The main site works fine without it.

The merge is `ffmpeg -c copy` — a stream copy, not a re-encode. Nothing is re-compressed, so
quality is exactly the source and the CPU cost is negligible; the worker is essentially a pipe.

## Run it

```bash
docker build -t alldownloader-mux .
docker run -p 8080:8080 -e DOWNLOAD_SECRET=<same secret as the site> alldownloader-mux
```

Deploys as-is to Fly.io, Railway, Render, or any host that allows long-lived responses. Two
requirements: it must use the **same `DOWNLOAD_SECRET`** as the Vercel project (that is how it
trusts incoming links), and the host must not impose a short response timeout — a big merge
streams for as long as the download takes.

Then point the site at it:

```bash
vercel env add MUX_URL      # e.g. https://mux.example.com
```

## Endpoints

- `GET /health` — returns `{"ok": true}`.
- `GET /mux?t=<token>` — streams the merged file. The token is HMAC-signed by `/api/resolve`;
  invalid, expired, or non-merge tokens are rejected with 403/410/400.

Output is fragmented MP4 when both codecs are MP4-compatible, Matroska otherwise, chosen by
the site when it builds the token.
