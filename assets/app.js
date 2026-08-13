// AllDownloader front-end. No framework, no build step.
'use strict';

const $ = (sel) => document.querySelector(sel);
const form = $('#form');
const urlInput = $('#url');
const goButton = $('#go');
const statusBox = $('#status');
const resultBox = $('#result');
const chips = [...document.querySelectorAll('.platforms li')];

const PLATFORM_HOSTS = {
  youtube: ['youtube.com', 'youtu.be'],
  instagram: ['instagram.com', 'instagr.am'],
  tiktok: ['tiktok.com'],
  soundcloud: ['soundcloud.com', 'snd.sc'],
  spotify: ['spotify.com', 'spotify.link'],
};

let playlistState = null; // { title, entries } — so we can go back after drilling in

/* ---------------- helpers ---------------- */

const el = (tag, props = {}, children = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
};

function detectPlatform(value) {
  let host;
  try {
    host = new URL(value.trim()).hostname.replace(/^www\./, '').toLowerCase();
  } catch {
    return null;
  }
  for (const [name, domains] of Object.entries(PLATFORM_HOSTS)) {
    if (domains.some((d) => host === d || host.endsWith('.' + d))) return name;
  }
  return null;
}

function formatSize(bytes) {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (!seconds) return '';
  const total = Math.round(seconds);
  const parts = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  return (parts[0] ? parts : parts.slice(1))
    .map((n, i) => (i === 0 ? String(n) : String(n).padStart(2, '0')))
    .join(':');
}

function setStatus(message, kind) {
  statusBox.hidden = !message;
  statusBox.className = 'status' + (kind ? ' ' + kind : '');
  statusBox.textContent = message || '';
}

function highlightPlatform() {
  const platform = detectPlatform(urlInput.value);
  chips.forEach((chip) => chip.classList.toggle('on', chip.dataset.platform === platform));
}

/* ---------------- rendering ---------------- */

function optionSubtitle(option) {
  const bits = [];
  if (option.kind === 'video') {
    if (option.codec) bits.push(option.codec);
    if (option.audio_codec) bits.push(option.audio_codec);
  } else if (option.codec) {
    bits.push(option.codec);
  }
  bits.push(option.ext.toUpperCase());
  return bits.filter(Boolean).join(' · ');
}

function badges(option) {
  const out = [];
  if (option.hdr) out.push(el('span', { className: 'badge hi', textContent: 'HDR' }));
  if (option.kind === 'video' && (option.height || 0) >= 2160) {
    out.push(el('span', { className: 'badge hi', textContent: option.height >= 4320 ? '8K' : 'UHD' }));
  }
  if (option.needs_mux && !option.mux_available) {
    out.push(el('span', { className: 'badge', textContent: '2 files' }));
  }
  if (option.kind === 'video' && !option.muxed && !option.needs_mux) {
    out.push(el('span', { className: 'badge', textContent: 'no sound' }));
  }
  return out;
}

function downloadRow(option, { quick = false } = {}) {
  const title = quick ? option.pick : option.label;
  const subtitle = quick ? `${option.label} · ${optionSubtitle(option)}` : optionSubtitle(option);

  const name = el('div', { className: 'name' }, [document.createTextNode(title), ...badges(option)]);
  const lead = el('div', { className: 'lead' }, [
    name,
    el('div', { className: 'sub', textContent: subtitle }),
  ]);
  const meta = [lead, el('span', { className: 'size', textContent: formatSize(option.filesize) })];

  // No mux worker configured: video and audio come down as two separate files.
  if (option.parts) {
    const details = el('details', { className: 'parts' });
    const summary = el('summary', { className: 'opt' + (quick ? ' quick' : '') });
    summary.append(...meta, el('span', { className: 'arrow', textContent: '▾' }));
    details.append(summary);
    const card = el('div', { className: 'card' });
    option.parts.forEach((part) => {
      const link = el('a', { className: 'opt', href: part.url, download: '' });
      link.append(
        el('div', { className: 'lead' }, [el('div', { className: 'name', textContent: part.label })]),
        el('span', { className: 'size', textContent: formatSize(part.filesize) }),
        el('span', { className: 'arrow', textContent: '↓' }),
      );
      card.append(link);
    });
    card.append(
      el('div', { className: 'note', style: 'padding:10px 14px' }, [
        'At this quality the site stores picture and sound separately, so they arrive as two files. '
          + 'Pick a lower quality for a single ready-to-play file.',
      ]),
    );
    details.append(card);
    return details;
  }

  const link = el('a', { className: 'opt' + (quick ? ' quick' : ''), href: option.url, download: '' });
  link.append(...meta, el('span', { className: 'arrow', textContent: '↓' }));
  return link;
}

function renderMedia(data) {
  const meta = el('div', { className: 'meta' }, [
    el('h2', { textContent: data.title }),
    el('p', {
      textContent: [data.uploader, formatDuration(data.duration)].filter(Boolean).join(' · '),
    }),
  ]);
  const media = el('div', { className: 'media' });
  if (data.thumbnail) {
    const thumb = el('img', { src: data.thumbnail, alt: '', loading: 'lazy', referrerPolicy: 'no-referrer' });
    // Some CDNs block hotlinking or simply never answer — drop the box either way.
    const drop = () => thumb.remove();
    thumb.addEventListener('error', drop);
    thumb.addEventListener('load', () => clearTimeout(timer));
    const timer = setTimeout(() => !thumb.complete && drop(), 6000);
    media.append(thumb);
  }
  media.append(meta);
  return media;
}

function renderResult(data) {
  resultBox.replaceChildren();
  resultBox.hidden = false;

  if (playlistState) {
    const back = el('button', { className: 'ghost', textContent: '← Back to ' + playlistState.title });
    back.addEventListener('click', () => renderPlaylist(playlistState, false));
    resultBox.append(el('div', { style: 'margin-bottom:12px' }, [back]));
  }

  const card = el('div', { className: 'card' }, [renderMedia(data)]);
  (data.quick || []).forEach((option) => card.append(downloadRow(option, { quick: true })));
  if (!(data.quick || []).length) {
    (data.options || []).slice(0, 3).forEach((option) => card.append(downloadRow(option)));
  }
  resultBox.append(card);

  const rest = data.options || [];
  if (rest.length) {
    const details = el('details');
    details.append(el('summary', { textContent: `All formats (${rest.length})` }));
    const list = el('div', { className: 'card' });
    rest.forEach((option) => list.append(downloadRow(option)));
    details.append(list);
    resultBox.append(details);
  }

  if (data.note) resultBox.append(el('p', { className: 'note', textContent: data.note }));
  if (data.insecure) {
    resultBox.append(
      el('p', {
        className: 'note warn',
        textContent: 'DOWNLOAD_SECRET is not set — anyone could forge download links against this deployment. Set it in your Vercel project settings.',
      }),
    );
  }
}

function renderPlaylist(data, remember = true) {
  if (remember) playlistState = data;
  resultBox.replaceChildren();
  resultBox.hidden = false;
  setStatus('');

  const card = el('div', { className: 'card' });
  card.append(
    el('div', { className: 'media' }, [
      el('div', { className: 'meta' }, [
        el('h2', { textContent: data.title }),
        el('p', { textContent: `${data.count} item${data.count === 1 ? '' : 's'} · tap one to download` }),
      ]),
    ]),
  );

  data.entries.forEach((entry, index) => {
    const button = el('button', { className: 'entry', type: 'button' });
    button.append(
      el('span', { className: 'idx', textContent: String(index + 1) }),
      el('div', { className: 'lead' }, [
        el('div', { className: 'name', textContent: entry.title }),
        el('div', {
          className: 'sub',
          textContent: [entry.uploader, formatDuration(entry.duration)].filter(Boolean).join(' · '),
        }),
      ]),
      el('span', { className: 'arrow', textContent: '›' }),
    );
    button.addEventListener('click', () => resolve(entry.url, { keepPlaylist: true }));
    card.append(button);
  });

  resultBox.append(card);
}

/* ---------------- network ---------------- */

async function resolve(link, { keepPlaylist = false } = {}) {
  if (!keepPlaylist) playlistState = null;
  resultBox.hidden = true;
  goButton.disabled = true;
  setStatus('Reading the link…', 'load');

  try {
    const response = await fetch('/api/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: link }),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok || !data.ok) {
      setStatus(data.error || `Something went wrong (${response.status}).`, 'error');
      return;
    }
    setStatus('');
    if (data.type === 'playlist') renderPlaylist(data);
    else renderResult(data);
    resultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch {
    setStatus('Network error — check your connection and try again.', 'error');
  } finally {
    goButton.disabled = false;
  }
}

/* ---------------- events ---------------- */

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const link = urlInput.value.trim();
  if (link) resolve(link);
});

urlInput.addEventListener('input', highlightPlatform);

$('#paste').addEventListener('click', async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return;
    urlInput.value = text;
    highlightPlatform();
    if (detectPlatform(text)) resolve(text);
  } catch {
    urlInput.focus();
    setStatus('Clipboard access was blocked — paste into the box manually.', 'error');
  }
});

// Allow ?url=... deep links and share-target style hand-offs.
const shared = new URLSearchParams(location.search).get('url');
if (shared) {
  urlInput.value = shared;
  highlightPlatform();
  resolve(shared);
}
