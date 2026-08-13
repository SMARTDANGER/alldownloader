# AllDownloader

Paste a link, pick a quality, download. One page, no framework, deploys to Vercel.

Supports **YouTube** (up to 8K/HDR), **Instagram**, **TikTok** (watermark-free, with the
codec shown per option), **SoundCloud**, and **Spotify** (metadata matching — see below).

- The page is plain HTML/CSS/JS — about 20 KB total, no build step, no npm install.
- Mobile-first: big tap targets, works one-handed, a **Paste** button that resolves as
  soon as you tap it, and `?url=…` deep links so you can wire it to a share shortcut.
- Three big buttons for the common cases (**Best quality**, **Most compatible**,
  **Audio only**) and a collapsed **All formats** list when you want to pick precisely.
- Playlists, albums and sets expand into a tappable track list.

## Deploy

```bash
npm i -g vercel
vercel            # preview
vercel --prod     # production
```

Or import the repo at [vercel.com/new](https://vercel.com/new). No build settings needed —
Vercel serves the static files and builds `api/` automatically.

**Set `DOWNLOAD_SECRET` before you go public.** Download links are HMAC-signed with it, which
is what stops the deployment being used as an open proxy for arbitrary URLs:

```bash
vercel env add DOWNLOAD_SECRET      # paste any long random string
```

The site warns you on screen while it is unset.

### Environment variables

| Variable | Required | What it does |
| --- | --- | --- |
| `DOWNLOAD_SECRET` | **yes, in production** | Signs download links. Any long random string. |
| `YTDLP_COOKIES` | for YouTube / Instagram | Netscape-format `cookies.txt` contents. Most YouTube and Instagram links need this from a datacenter IP. |
| `YTDLP_PROXY` | sometimes | `http://user:pass@host:port`. Use a residential proxy when a site blocks the server's IP outright (TikTok does this often). |
| `MUX_URL` | optional | Base URL of a merge worker (see below) so 8K arrives as one file. |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | for Spotify albums & playlists | Free credentials from the [Spotify dashboard](https://developer.spotify.com/dashboard). Single tracks work without them. |
| `YTDLP_USER_AGENT` | optional | Override the User-Agent sent to sites. |
| `TOKEN_TTL` | optional | Download-link lifetime in seconds (default 21600). |

**About cookies:** Vercel runs on datacenter IPs, and YouTube in particular answers those with
"Sign in to confirm you're not a bot". Export cookies from a logged-in browser
([yt-dlp's guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)),
paste the file contents into `YTDLP_COOKIES`, and use a throwaway account — the cookies are as
good as a login. If a site is blocking the IP itself rather than challenging it, cookies won't
help and you need `YTDLP_PROXY`.

## How it works

```
browser ──POST /api/resolve──▶  Python function (yt-dlp)
                                 └─ returns formats + HMAC-signed links
browser ──GET  /api/download──▶  Edge function
                                 └─ streams the media through with
                                    Content-Disposition: attachment
```

The download endpoint runs on the **Edge runtime** deliberately: it pipes the response body
straight through instead of buffering it, so the 4.5 MB serverless response limit does not
apply and multi-gigabyte files work. `Range` requests are forwarded, so downloads resume and
seek normally.

Proxying (rather than linking straight to the CDN) is what makes the file actually save with a
proper name — TikTok and Instagram CDNs reject requests without their original headers, and
cross-origin links ignore the browser's `download` attribute.

Links expire after six hours; resolve the page again to get fresh ones.

## About 8K and merged files

Above roughly 1080p, YouTube stops shipping video and audio in one file — the high-resolution
streams (8K, 4K, HDR, AV1) are picture-only by design. Joining them needs ffmpeg, and Vercel
functions can neither ship ffmpeg nor stream a multi-gigabyte remux inside the execution limit.

So there are two modes, and the site is honest about which one you are in:

- **Out of the box:** those qualities download as two files, labelled *2 files*, with the video
  and audio parts listed separately. Any player or a one-line `ffmpeg -i video.mp4 -i audio.m4a
  -c copy out.mp4` joins them. Everything at 1080p and below is already a single file.
- **With `MUX_URL` set:** the same choices arrive as one finished file. Deploy the small
  container in [`worker/`](worker/) anywhere that allows long responses (Fly.io, Railway,
  Render, a VPS), give it the *same* `DOWNLOAD_SECRET`, and point `MUX_URL` at it. The merge is
  a stream copy — no re-encoding, so nothing is lost and it runs at network speed.

## Per-platform notes

**YouTube** — full quality ladder up to 8K60 and HDR, plus 129 kbps AAC / 160 kbps Opus audio.
Playlists expand into a track list. Needs `YTDLP_COOKIES` in practice.

**TikTok** — downloads are watermark-free. Each option shows its codec, so you can take the
H.264 version for maximum compatibility or the HEVC/higher-bitrate one for the best picture;
*Best quality* picks the highest-bitrate stream whatever the codec, and *Most compatible*
picks the best H.264 MP4.

**Instagram** — posts, reels and stories. Private accounts need `YTDLP_COOKIES`.

**SoundCloud** — tracks and sets. Tracks published with SoundCloud's DRM cannot be downloaded
and are reported as such.

**Spotify** — Spotify's own audio is DRM-protected and this does not touch it. What it does is
read the track's metadata (title, artist, duration) and find the closest matching audio source,
the same approach spotDL takes; the result is labelled so you know it is a match rather than
the Spotify file. Single tracks work with no setup; albums and playlists need the two
`SPOTIFY_*` variables.

## Local development

```bash
pip install yt-dlp
vercel dev          # http://localhost:3000
```

`vercel dev` runs both the Python and Edge functions locally. Set `DOWNLOAD_SECRET` in a
`.env` file first, otherwise the site runs with a placeholder secret and says so.

## Limits worth knowing

- **Bandwidth is yours.** Every download flows through your deployment, so it counts against
  your Vercel usage. A handful of 8K downloads will eat a Hobby plan's allowance — put it
  behind auth, or run the traffic through the merge worker's host, if you expect real use.
- **Resolving takes a second or two** per link; the Python function is capped at 60 s.
- **Extractors break** when sites change. `requirements.txt` deliberately leaves `yt-dlp`
  unpinned — redeploy to pick up a fresh version, which fixes most breakage.
- Only progressive HTTP streams are offered. Live streams and HLS-only sources are filtered
  out rather than shown as broken options.

## Legal

Download only what you have the right to download. Respect each platform's terms of service
and your local copyright law. You are responsible for what you and your users do with a
deployment of this.
