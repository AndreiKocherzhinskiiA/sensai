# Publishing a chat

The **Share** button saves a snapshot of the chat and gives you a link, plus PDF, Markdown and HTML downloads.

## Recommended: publish to Vercel

`publish_service/` is a small Vercel project that stores shared chats in Vercel Blob and serves them at a permanent `https://….vercel.app/p/<id>` link. Deploy it once (steps in `publish_service/README.md`), then set:

```powershell
$env:SENSAI_PUBLISH_URL = "https://<your-project>.vercel.app"
$env:SENSAI_PUBLISH_TOKEN = "<the secret you set as PUBLISH_TOKEN>"
```

Share then uploads the rendered page, PDF and Markdown. The link works without your computer running. If you are offline or the upload fails, Sensai falls back to a local link and says why.

## Other ways to make a link work on the web

A link built from `http://localhost:8000` only works on your own computer. Sensai marks such links with a warning. Use one of these ways to get a public link.

### Option 1: open Sensai through a public address

Start Sensai, then start a tunnel in a second terminal (no account needed):

```bash
cloudflared tunnel --url http://localhost:8000
```

Open the `https://….trycloudflare.com` address that it prints and use Sensai from there. Share links then use that address.

### Option 2: set `SENSAI_PUBLIC_URL`

If you open Sensai as `localhost` but it is reachable from outside (tunnel, reverse proxy, cloud host), tell it the public address:

```bash
# Linux / macOS / WSL
SENSAI_PUBLIC_URL=https://my-sensai.example.com python -m web.app
# PowerShell
$env:SENSAI_PUBLIC_URL = "https://my-sensai.example.com"; python -m web.app
```

The value must start with `http://` or `https://`. Links are then always built from it.

## Anyone can read a published link

Snapshots at `/p/<id>` are public to anyone who has the link. They are marked `noindex`, but they are not password protected. Do not publish private chats.

## Downloads

- **PDF**: styled A4 document with page numbers, code blocks and lists.
- **Markdown** and **HTML**: the HTML file is one self-contained page that works offline and follows the reader's light or dark theme.
- Add `&download=1` to a Markdown or HTML export URL to force a file download.
