"""Rendering helpers for published snapshots: a small safe Markdown subset, a styled HTML page and a styled PDF.

Standard library only. All text is escaped before any markup is added.
"""
import html
import re
import time
from typing import Dict, List, Optional, Tuple

ROLE_LABELS = {'user': 'You', 'assistant': 'Sensai'}

# ---------------------------------------------------------------- Markdown subset

Block = Tuple  # ('h', level, text) ('p', text) ('ul', [items]) ('ol', [items]) ('code', lang, text) ('quote', text) ('hr',)

_FENCE = re.compile(r'^\s*```\s*([\w+-]*)\s*$')
_HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_UL = re.compile(r'^\s*[-*+]\s+(.*)$')
_OL = re.compile(r'^\s*\d+[.)]\s+(.*)$')
_HR = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')


def parse_blocks(text: str) -> List[Block]:
    lines = (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks: List[Block] = []
    para: List[str] = []
    i = 0

    def flush():
        if para:
            blocks.append(('p', ' '.join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush()
            lang, code = fence.group(1), []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            blocks.append(('code', lang, '\n'.join(code)))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if _HR.match(line):
            flush()
            blocks.append(('hr',))
        elif _HEADING.match(line):
            flush()
            m = _HEADING.match(line)
            blocks.append(('h', len(m.group(1)), m.group(2)))
        elif _UL.match(line) or _OL.match(line):
            flush()
            ordered = bool(_OL.match(line))
            pattern = _OL if ordered else _UL
            items = []
            while i < len(lines) and pattern.match(lines[i]):
                items.append(pattern.match(lines[i]).group(1).strip())
                i += 1
            blocks.append(('ol' if ordered else 'ul', items))
            continue
        elif line.lstrip().startswith('>'):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(('quote', ' '.join(quote)))
            continue
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


_INLINE = re.compile(
    r'(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\([^)\s]+\))'
)


def inline_html(text: str) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        token = m.group(0)
        if token.startswith('`'):
            out.append(f'<code>{html.escape(token[1:-1])}</code>')
        elif token.startswith('**') or token.startswith('__'):
            out.append(f'<strong>{html.escape(token[2:-2])}</strong>')
        elif token.startswith('*'):
            out.append(f'<em>{html.escape(token[1:-1])}</em>')
        else:
            label, url = re.match(r'\[([^\]]+)\]\(([^)]+)\)', token).groups()
            if re.match(r'^https?://', url, re.I):
                out.append(f'<a href="{html.escape(url, quote=True)}" rel="noopener nofollow" target="_blank">{html.escape(label)}</a>')
            else:
                out.append(html.escape(label))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return ''.join(out)


def strip_inline(text: str) -> str:
    text = re.sub(r'\[([^\]\n]+)\]\(([^)\s]+)\)', lambda m: f'{m.group(1)} ({m.group(2)})', text)
    text = re.sub(r'(\*\*|__)(.+?)\1', r'\2', text)
    text = re.sub(r'(?<![\w*])\*([^*\s][^*\n]*)\*', r'\1', text)
    return text.replace('`', '')


def blocks_to_html(blocks: List[Block]) -> str:
    parts = []
    for block in blocks:
        kind = block[0]
        if kind == 'h':
            level = min(6, block[1] + 2)  # keep page h1/h2 for structure
            parts.append(f'<h{level}>{inline_html(block[2])}</h{level}>')
        elif kind == 'p':
            parts.append(f'<p>{inline_html(block[1])}</p>')
        elif kind in ('ul', 'ol'):
            items = ''.join(f'<li>{inline_html(item)}</li>' for item in block[1])
            parts.append(f'<{kind}>{items}</{kind}>')
        elif kind == 'code':
            lang = html.escape(block[1]) if block[1] else ''
            label = f'<span class="lang">{lang}</span>' if lang else ''
            parts.append(f'<pre>{label}<code>{html.escape(block[2])}</code></pre>')
        elif kind == 'quote':
            parts.append(f'<blockquote>{inline_html(block[1])}</blockquote>')
        elif kind == 'hr':
            parts.append('<hr>')
    return '\n'.join(parts)


def format_date(ts: Optional[float]) -> str:
    return time.strftime('%d %b %Y', time.localtime(ts or time.time())).lstrip('0')


# ---------------------------------------------------------------- HTML page

_CSS = '''
:root{--bg:#f7f5f2;--panel:#fff;--ink:#1d1b19;--ink2:#55504b;--ink3:#8a847d;--line:#e6e1da;--acc:#d9532f;--acc-wash:#fbece6;--user:#f0ebe4;--code:#1f1d1b;--code-ink:#f1ece6}
@media (prefers-color-scheme:dark){:root{--bg:#161412;--panel:#1e1b19;--ink:#f1ece6;--ink2:#bdb6ae;--ink3:#857e76;--line:#322e2a;--acc:#f0714d;--acc-wash:#2a1b16;--user:#2a2623;--code:#100e0d;--code-ink:#f1ece6}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:46rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
.top{display:flex;align-items:center;justify-content:space-between;gap:1rem;color:var(--ink3);font-size:.85rem}
.brand{display:flex;align-items:center;gap:.55rem;color:var(--ink);font-weight:650;letter-spacing:-.01em}
.brand svg{width:26px;height:26px;color:var(--acc)}
h1{margin:1.6rem 0 .35rem;font-size:clamp(1.7rem,4.5vw,2.3rem);line-height:1.15;letter-spacing:-.03em;text-wrap:balance}
.meta{margin:0 0 2rem;color:var(--ink3);font-size:.9rem}
.msg{display:flex;flex-direction:column;gap:.35rem;margin:1.5rem 0}
.who{font-size:.76rem;font-weight:650;letter-spacing:.07em;text-transform:uppercase;color:var(--ink3)}
.msg.assistant .who{color:var(--acc)}
.bubble{overflow-wrap:anywhere}
.msg.user{align-items:flex-end}
.msg.user .bubble{max-width:85%;padding:.7rem 1rem;border-radius:16px 16px 4px 16px;background:var(--user);border:1px solid var(--line)}
.msg.assistant .bubble{padding-left:1rem;border-left:2px solid var(--acc)}
.bubble>*:first-child{margin-top:0}.bubble>*:last-child{margin-bottom:0}
.bubble p{margin:.7em 0}
.bubble h3,.bubble h4,.bubble h5,.bubble h6{margin:1.2em 0 .4em;line-height:1.3;letter-spacing:-.01em}
.bubble ul,.bubble ol{margin:.7em 0;padding-left:1.4rem}.bubble li{margin:.25em 0}
.bubble a{color:var(--acc);text-underline-offset:3px}
.bubble code{font:.88em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--acc-wash);padding:.12em .38em;border-radius:5px}
.bubble pre{position:relative;margin:1em 0;padding:1rem 1.1rem;border-radius:12px;background:var(--code);color:var(--code-ink);overflow-x:auto;line-height:1.55}
.bubble pre code{background:none;padding:0;font-size:.85rem;color:inherit}
.bubble pre .lang{position:absolute;top:.4rem;right:.7rem;font:600 .68rem ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;color:#8d857c}
.bubble blockquote{margin:1em 0;padding:.1em 1rem;border-left:3px solid var(--line);color:var(--ink2)}
.bubble hr{border:0;border-top:1px solid var(--line);margin:1.4em 0}
.section{margin:2rem 0;padding:1.4rem 1.5rem;background:var(--panel);border:1px solid var(--line);border-radius:14px}
.section h2{margin:0 0 .6rem;font-size:1.2rem;letter-spacing:-.02em}
.foot{margin-top:3.5rem;padding-top:1.2rem;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:.6rem 1.4rem;justify-content:space-between;color:var(--ink3);font-size:.85rem}
.foot a{color:var(--ink2);text-underline-offset:3px}
@media print{body{background:#fff;color:#000}.wrap{max-width:100%;padding:0}.foot a{display:none}.msg{break-inside:avoid}.bubble pre{white-space:pre-wrap;border:1px solid #ccc;background:#f6f6f6;color:#111}}
'''

_ENSO = ('<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-linecap="round" aria-hidden="true">'
         '<path stroke-width="3.4" d="M25.5 10.5A11 11 0 1 0 25.5 21.5"/><circle cx="16" cy="16" r="2.6" fill="currentColor" stroke="none"/></svg>')


def render_page(snap: Dict, export_base: Optional[str] = None) -> str:
    title = html.escape(snap['title'])
    body = []
    if snap['type'] == 'conversation':
        for msg in snap['data'].get('messages', []):
            role = msg.get('role', 'user')
            cls = 'user' if role == 'user' else 'assistant'
            label = html.escape(ROLE_LABELS.get(role, role.capitalize()))
            body.append(f'<div class="msg {cls}"><div class="who">{label}</div>'
                        f'<div class="bubble">{blocks_to_html(parse_blocks(msg.get("content", "")))}</div></div>')
        count = len(snap['data'].get('messages', []))
        meta = f'{count} message{"s" if count != 1 else ""}'
    else:
        for sec_id, content in snap['data'].get('sections', {}).items():
            body.append(f'<div class="section"><h2>{html.escape(sec_id)}</h2>'
                        f'<div class="bubble">{blocks_to_html(parse_blocks(content))}</div></div>')
        meta = 'Document'
    links = ''
    if export_base:
        base = html.escape(export_base, quote=True)
        links = ('<span>Download: '
                 f'<a href="{base}/export?format=pdf">PDF</a> · '
                 f'<a href="{base}/export?format=md&download=1">Markdown</a> · '
                 f'<a href="{base}/export?format=html&download=1">HTML</a></span>')
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="light dark">
<meta property="og:title" content="{title}">
<meta property="og:description" content="Shared from Sensai">
<title>{title} · Sensai</title>
<style>{_CSS}</style>
</head>
<body>
<main class="wrap">
  <header class="top"><span class="brand">{_ENSO}Sensai</span><span>{html.escape(format_date(snap.get("created_at")))}</span></header>
  <h1>{title}</h1>
  <p class="meta">{html.escape(meta)}</p>
  {chr(10).join(body)}
  <footer class="foot"><span>Published with Sensai</span>{links}</footer>
</main>
</body>
</html>
'''


# ---------------------------------------------------------------- Markdown export

def render_markdown(snap: Dict) -> str:
    lines = [f"# {snap['title']}", '', f"*Shared from Sensai · {format_date(snap.get('created_at'))}*", '']
    if snap['type'] == 'conversation':
        messages = snap['data'].get('messages', [])
        for index, msg in enumerate(messages):
            lines += [f"### {msg.get('role', 'user').capitalize()}", '', (msg.get('content', '') or '').strip(), '']
            if index < len(messages) - 1:
                lines += ['---', '']
    else:
        for sec_id, content in snap['data'].get('sections', {}).items():
            lines += [f'## {sec_id}', '', (content or '').strip(), '']
    return '\n'.join(lines).strip() + '\n'


# ---------------------------------------------------------------- PDF

# Helvetica advance widths (1/1000 em) for ASCII 32..126.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
         1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
         333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN_X, TOP, BOTTOM = 56.0, 800.0, 64.0
TEXT_W = PAGE_W - 2 * MARGIN_X

_ACCENT = (0.851, 0.325, 0.184)
_INK = (0.114, 0.106, 0.098)
_MUTED = (0.54, 0.52, 0.49)
_RULE = (0.90, 0.88, 0.85)


def _text_width(text: str, size: float, font: str) -> float:
    if font == 'mono':
        return 0.6 * size * len(text)
    scale = 1.07 if font == 'bold' else 1.0
    total = sum(_HELV[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in text)
    return total * size * scale / 1000.0


def _wrap(text: str, size: float, font: str, width: float) -> List[str]:
    lines, cur = [], ''
    for word in text.split():
        while _text_width(word, size, font) > width:  # break very long words
            cut = len(word)
            while cut > 1 and _text_width(word[:cut], size, font) > width:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ''
            lines.append(word[:cut])
            word = word[cut:]
        trial = f'{cur} {word}'.strip()
        if cur and _text_width(trial, size, font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or ['']


def _pdf_str(text: str) -> bytes:
    raw = text.replace('→', '->').replace('✓', 'v').encode('cp1252', errors='replace')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


class _Pdf:
    def __init__(self, title: str):
        self.title = title
        self.pages: List[List[bytes]] = []
        self.ops: List[bytes] = []
        self.y = TOP
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = TOP

    def ensure(self, height: float):
        if self.y - height < BOTTOM:
            self.new_page()

    def rgb(self, color, stroke=False):
        return ('%.3f %.3f %.3f %s' % (*color, 'RG' if stroke else 'rg')).encode()

    def text(self, x: float, y: float, s: str, size: float, font: str = 'regular', color=_INK, spacing: float = 0):
        tag = {'regular': 'F1', 'bold': 'F2', 'mono': 'F3'}[font]
        self.ops.append(self.rgb(color))
        tc = f' {spacing:.2f} Tc'  # Tc persists in the graphics state, so always reset it
        self.ops.append(f'BT /{tag} {size:g} Tf{tc} {x:.1f} {y:.1f} Td ('.encode() + _pdf_str(s) + b') Tj ET')

    def rect(self, x, y, w, h, color):
        self.ops.append(self.rgb(color))
        self.ops.append(f'{x:.1f} {y:.1f} {w:.1f} {h:.1f} re f'.encode())

    def hline(self, y, color=_RULE, x1=MARGIN_X, x2=PAGE_W - MARGIN_X, width=0.6):
        self.ops.append(self.rgb(color, stroke=True))
        self.ops.append(f'{width} w {x1:.1f} {y:.1f} m {x2:.1f} {y:.1f} l S'.encode())

    def paragraph(self, text: str, size=10.5, font='regular', color=_INK, indent=0.0, leading=None, gap=6.0):
        leading = leading or size * 1.5
        for line in _wrap(strip_inline(text), size, font, TEXT_W - indent):
            self.ensure(leading)
            self.y -= leading
            self.text(MARGIN_X + indent, self.y, line, size, font, color)
        self.y -= gap

    def code(self, code: str):
        size, leading, pad = 8.8, 12.6, 8.0
        chars = int((TEXT_W - 2 * pad) / (0.6 * size))
        rows: List[str] = []
        for raw in (code or '').expandtabs(4).split('\n') or ['']:
            while len(raw) > chars:
                rows.append(raw[:chars])
                raw = raw[chars:]
            rows.append(raw)
        i = 0
        while i < len(rows):
            self.ensure(leading + 2 * pad)
            fit = max(1, int((self.y - BOTTOM - 2 * pad) / leading))
            chunk = rows[i:i + fit]
            height = len(chunk) * leading + 2 * pad
            self.rect(MARGIN_X, self.y - height, TEXT_W, height, (0.955, 0.945, 0.93))
            ty = self.y - pad
            for row in chunk:
                ty -= leading
                self.text(MARGIN_X + pad, ty + 3.2, row, size, 'mono', (0.16, 0.15, 0.14))
            self.y -= height
            i += fit
        self.y -= 8.0

    def bullets(self, items, ordered=False):
        size, leading = 10.5, 15.5
        for n, item in enumerate(items, 1):
            marker = f'{n}.' if ordered else '•'
            lines = _wrap(strip_inline(item), size, 'regular', TEXT_W - 18)
            self.ensure(leading)
            self.y -= leading
            self.text(MARGIN_X + 4, self.y, marker, size, 'regular', _ACCENT)
            self.text(MARGIN_X + 18, self.y, lines[0], size)
            for line in lines[1:]:
                self.ensure(leading)
                self.y -= leading
                self.text(MARGIN_X + 18, self.y, line, size)
        self.y -= 6.0

    def quote(self, text: str):
        lines = _wrap(strip_inline(text), 10.5, 'regular', TEXT_W - 16)
        for line in lines:
            self.ensure(15.5)
            self.y -= 15.5
            self.rect(MARGIN_X, self.y - 4, 2.2, 15.5, _RULE)
            self.text(MARGIN_X + 12, self.y, line, 10.5, 'regular', (0.38, 0.36, 0.34))
        self.y -= 8.0

    def blocks(self, blocks):
        for b in blocks:
            kind = b[0]
            if kind == 'h':
                size = {1: 15, 2: 13.5, 3: 12}.get(b[1], 11)
                self.ensure(size * 2.4)
                self.y -= 6
                self.paragraph(b[2], size, 'bold', gap=3)
            elif kind == 'p':
                self.paragraph(b[1])
            elif kind in ('ul', 'ol'):
                self.bullets(b[1], kind == 'ol')
            elif kind == 'code':
                self.code(b[2])
            elif kind == 'quote':
                self.quote(b[1])
            elif kind == 'hr':
                self.ensure(12)
                self.y -= 6
                self.hline(self.y)
                self.y -= 8

    def build(self, subtitle: str) -> bytes:
        total = len(self.pages)
        for n, ops in enumerate(self.pages, 1):
            self.ops = ops
            self.hline(BOTTOM - 14)
            self.text(MARGIN_X, BOTTOM - 28, 'Sensai', 8, 'bold', _ACCENT)
            self.text(MARGIN_X + 40, BOTTOM - 28, self.title[:70], 8, 'regular', _MUTED)
            label = f'Page {n} of {total}'
            self.text(PAGE_W - MARGIN_X - _text_width(label, 8, 'regular'), BOTTOM - 28, label, 8, 'regular', _MUTED)

        objects: Dict[int, bytes] = {}
        info_id = 7
        first_page = 8
        page_ids = [first_page + 2 * i for i in range(total)]
        objects[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
        objects[2] = ('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(f'{p} 0 R' for p in page_ids), total)).encode()
        for oid, name in ((3, 'Helvetica'), (4, 'Helvetica-Bold'), (5, 'Courier')):
            objects[oid] = f'<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>'.encode()
        objects[6] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>'
        objects[info_id] = b'<< /Title (' + _pdf_str(self.title) + b') /Producer (Sensai) /Subject (' + _pdf_str(subtitle) + b') >>'
        for i, ops in enumerate(self.pages):
            stream = b'\n'.join(ops)
            pid, cid = page_ids[i], page_ids[i] + 1
            objects[pid] = (f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] '
                            f'/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R >> >> /Contents {cid} 0 R >>').encode()
            objects[cid] = f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'\nendstream'
        max_id = max(objects)
        out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        offsets = {}
        for oid in range(1, max_id + 1):
            offsets[oid] = len(out)
            out += f'{oid} 0 obj\n'.encode() + objects[oid] + b'\nendobj\n'
        xref = len(out)
        out += f'xref\n0 {max_id + 1}\n0000000000 65535 f \n'.encode()
        for oid in range(1, max_id + 1):
            out += f'{offsets[oid]:010d} 00000 n \n'.encode()
        out += f'trailer\n<< /Size {max_id + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
        return bytes(out)


def render_pdf(title: str, sections: List[Tuple[str, str]], date: Optional[float] = None, roles: bool = False) -> bytes:
    """sections: (heading, markdown body). With roles=True headings are chat roles drawn as coloured labels."""
    pdf = _Pdf(title)
    pdf.text(MARGIN_X, pdf.y - 8, 'SENSAI', 8.5, 'bold', _ACCENT, spacing=1.6)
    pdf.text(PAGE_W - MARGIN_X - _text_width(format_date(date), 9, 'regular'), pdf.y - 8, format_date(date), 9, 'regular', _MUTED)
    pdf.y -= 22
    for line in _wrap(title, 22, 'bold', TEXT_W):
        pdf.y -= 27
        pdf.text(MARGIN_X, pdf.y, line, 22, 'bold')
    pdf.y -= 12
    pdf.hline(pdf.y, _ACCENT, x2=MARGIN_X + 44, width=2.2)
    pdf.y -= 18
    for heading, body in sections:
        pdf.ensure(60)
        if heading:
            if roles:
                is_user = heading.lower() == 'user'
                label = ROLE_LABELS.get(heading.lower(), heading).upper()
                pdf.y -= 14
                pdf.text(MARGIN_X, pdf.y, label, 8.2, 'bold', _MUTED if is_user else _ACCENT, spacing=1.2)
                pdf.y -= 3
            else:
                pdf.y -= 4
                pdf.paragraph(heading, 14, 'bold', gap=2)
                pdf.hline(pdf.y + 1)
                pdf.y -= 6
        pdf.blocks(parse_blocks(body or ''))
        pdf.y -= 8
    return pdf.build(f'Shared from Sensai {format_date(date)}')
