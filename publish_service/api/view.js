import { get } from '@vercel/blob';
import { Readable } from 'node:stream';

const ID_RE = /^[A-Za-z0-9_-]{6,64}$/;

// Pages use inline CSS only. This policy blocks scripts even if stored HTML were ever tampered with.
const CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

const HOME = `<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>Sensai</title>
<body style="font:16px system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#333"><h1>Sensai</h1>
<p>Shared Sensai chats are published here. Open a link you were sent.</p></body>`;

export default async function handler(req, res) {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Robots-Tag', 'noindex');
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('Referrer-Policy', 'no-referrer');

  const query = req.query || {};
  if (query.home) {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.status(200).send(HOME);
  }
  const id = String(query.id || '');
  if (!ID_RE.test(id)) return res.status(404).send('Not found');

  const format = String(query.format || '');
  const download = String(query.download || '') === '1';
  let name;
  let type;
  let disposition = null;
  if (format === 'pdf') {
    name = 'export.pdf';
    type = 'application/pdf';
    disposition = `attachment; filename="${id}.pdf"`;
  } else if (format === 'md') {
    name = 'export.md';
    type = 'text/markdown; charset=utf-8';
    if (download) disposition = `attachment; filename="${id}.md"`;
  } else if (format === 'html' && download) {
    name = 'download.html';
    type = 'text/html; charset=utf-8';
    disposition = `attachment; filename="${id}.html"`;
  } else if (format === '' || format === 'html') {
    name = 'index.html';
    type = 'text/html; charset=utf-8';
  } else {
    return res.status(404).send('Not found');
  }

  let result;
  try {
    result = await get(`p/${id}/${name}`, { access: 'private' });
  } catch {
    return res.status(502).send('Storage unavailable');
  }
  if (!result || result.statusCode !== 200) return res.status(404).send('Not found');

  res.setHeader('Content-Type', type);
  if (disposition) res.setHeader('Content-Disposition', disposition);
  // Published content never changes, so it can be cached.
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.status(200);
  Readable.fromWeb(result.stream).pipe(res);
}
