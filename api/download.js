// GET /api/download?t=<signed token>
//
// Streams the resolved media back to the browser as a file attachment. It runs
// on the Edge runtime so the body is piped through instead of buffered — the
// 4.5 MB serverless response limit does not apply, and multi-gigabyte 8K files
// work. The token is HMAC-signed by /api/resolve, so this cannot be used as an
// open proxy for arbitrary URLs.

export const config = { runtime: 'edge' };

const DEV_SECRET = 'insecure-dev-secret-set-DOWNLOAD_SECRET';

function b64urlToBytes(value) {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((value.length + 3) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function verify(token) {
  const dot = token.lastIndexOf('.');
  if (dot < 1) throw new Error('Malformed token');

  const data = token.slice(0, dot);
  const signature = b64urlToBytes(token.slice(dot + 1));
  const secret = process.env.DOWNLOAD_SECRET || DEV_SECRET;

  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify'],
  );
  const valid = await crypto.subtle.verify('HMAC', key, signature, new TextEncoder().encode(data));
  if (!valid) throw new Error('Bad signature');

  const payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(data)));
  if (!payload.exp || payload.exp * 1000 < Date.now()) throw new Error('expired');
  if (!payload.u || !/^https?:\/\//i.test(payload.u)) throw new Error('Bad target');
  return payload;
}

function contentDisposition(name) {
  const fallback = (name || 'download').replace(/[^\x20-\x7e]/g, '_').replace(/["\\]/g, '');
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(name)}`;
}

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
}

export default async function handler(request) {
  const token = new URL(request.url).searchParams.get('t');
  if (!token) return json(400, { ok: false, error: 'Missing token.' });

  let payload;
  try {
    payload = await verify(token);
  } catch (error) {
    const expired = String(error.message).includes('expired');
    return json(expired ? 410 : 403, {
      ok: false,
      error: expired ? 'This download link expired — resolve the link again.' : 'Invalid download link.',
    });
  }

  const upstreamHeaders = new Headers();
  for (const [key, value] of Object.entries(payload.h || {})) {
    if (!/^(host|content-length|accept-encoding)$/i.test(key)) upstreamHeaders.set(key, value);
  }
  if (!upstreamHeaders.has('user-agent')) {
    upstreamHeaders.set('user-agent', request.headers.get('user-agent') || 'Mozilla/5.0');
  }
  // Forward Range so the browser can resume or seek.
  const range = request.headers.get('range');
  if (range) upstreamHeaders.set('range', range);

  let upstream;
  try {
    upstream = await fetch(payload.u, { headers: upstreamHeaders, redirect: 'follow' });
  } catch {
    return json(502, { ok: false, error: 'Could not reach the media server.' });
  }

  if (!upstream.ok && upstream.status !== 206) {
    return json(upstream.status === 403 ? 410 : 502, {
      ok: false,
      error:
        upstream.status === 403
          ? 'The media link expired. Resolve the page link again.'
          : `Media server responded ${upstream.status}.`,
    });
  }

  const headers = new Headers();
  for (const key of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag']) {
    const value = upstream.headers.get(key);
    if (value) headers.set(key, value);
  }
  if (!headers.has('accept-ranges')) headers.set('accept-ranges', 'bytes');
  headers.set('content-disposition', contentDisposition(payload.n || 'download'));
  headers.set('cache-control', 'no-store');
  headers.set('x-content-type-options', 'nosniff');

  return new Response(upstream.body, { status: upstream.status, headers });
}
