import { put } from '@vercel/blob';
import { createHash, timingSafeEqual } from 'node:crypto';

const ID_RE = /^[A-Za-z0-9_-]{6,64}$/;
const MAX_FIELD = 3_500_000; // Vercel functions accept ~4.5 MB bodies

function authorized(req) {
  const expected = process.env.PUBLISH_TOKEN || '';
  const given = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!expected || !given) return false;
  // Hash first so the comparison has equal length and constant time.
  const a = createHash('sha256').update(given).digest();
  const b = createHash('sha256').update(expected).digest();
  return timingSafeEqual(a, b);
}

function baseUrl(req) {
  const configured = (process.env.PUBLIC_BASE_URL || '').replace(/\/+$/, '');
  if (configured) return configured;
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  return `https://${host}`;
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Use POST.' });
  }
  if (!authorized(req)) return res.status(401).json({ error: 'Unauthorized.' });

  const body = req.body && typeof req.body === 'object' ? req.body : null;
  if (!body) return res.status(400).json({ error: 'Send a JSON body.' });

  const { id, html, html_download: htmlDownload, md, pdf_base64: pdfBase64 } = body;
  if (typeof id !== 'string' || !ID_RE.test(id)) {
    return res.status(422).json({ error: 'id must be 6-64 letters, digits, "-" or "_".' });
  }
  for (const [name, value] of Object.entries({ html, html_download: htmlDownload, md })) {
    if (typeof value !== 'string' || !value || value.length > MAX_FIELD) {
      return res.status(422).json({ error: `${name} is missing or too large.` });
    }
  }
  if (pdfBase64 !== undefined && (typeof pdfBase64 !== 'string' || pdfBase64.length > MAX_FIELD)) {
    return res.status(422).json({ error: 'pdf_base64 is invalid or too large.' });
  }

  const files = [
    ['index.html', html, 'text/html; charset=utf-8'],
    ['download.html', htmlDownload, 'text/html; charset=utf-8'],
    ['export.md', md, 'text/markdown; charset=utf-8'],
  ];
  if (pdfBase64) files.push(['export.pdf', Buffer.from(pdfBase64, 'base64'), 'application/pdf']);

  try {
    for (const [name, content, contentType] of files) {
      await put(`p/${id}/${name}`, content, {
        access: 'private',
        contentType,
        addRandomSuffix: false,
        allowOverwrite: false,
      });
    }
  } catch (error) {
    const message = String((error && error.message) || error);
    if (/already exists/i.test(message)) return res.status(409).json({ error: 'That id is already published.' });
    return res.status(502).json({ error: 'Storage failed.' });
  }
  return res.status(200).json({ id, url: `${baseUrl(req)}/p/${id}` });
}
