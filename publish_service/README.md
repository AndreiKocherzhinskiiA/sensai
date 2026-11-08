# Sensai publish service

A tiny Vercel project. The local Sensai app renders a chat (HTML, Markdown, PDF) and uploads it here.
This service stores the files in **Vercel Blob** (private store) and serves public pages at `/p/<id>`.

| Route | What |
| --- | --- |
| `POST /api/publish` | Needs `Authorization: Bearer <PUBLISH_TOKEN>`. Stores a snapshot and returns `{id, url}`. |
| `GET /p/<id>` | The styled page. |
| `GET /p/<id>/export?format=pdf` | PDF download. `format=md` or `format=html&download=1` also work. |

## Deploy

```bash
cd publish_service
npm install
vercel login
vercel link                                                # create or pick a project
vercel blob create-store sensai-publish --access private   # connect it to this project when asked
vercel env add PUBLISH_TOKEN production                    # paste a long random secret
vercel deploy --prod
```

Make a token with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Point Sensai at it

```powershell
$env:SENSAI_PUBLISH_URL = "https://<your-project>.vercel.app"
$env:SENSAI_PUBLISH_TOKEN = "<the same secret>"
python -m web.app
```

Or put both lines in `publish_service/.env.publish` (git-ignored); Sensai reads it automatically and environment variables take priority.

With both set, **Share** uploads to Vercel and returns the `vercel.app` link. If you are offline or the upload fails, Sensai falls back to a local link and shows a warning.

## Notes

- Pages are public to anyone with the link and marked `noindex`.
- Only people with `PUBLISH_TOKEN` can publish. Keep it out of the browser and out of git.
- Pages are served with a strict Content-Security-Policy (no scripts).
