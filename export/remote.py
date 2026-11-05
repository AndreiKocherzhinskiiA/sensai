"""Upload a rendered snapshot to the hosted publish service (see publish_service/)."""
import base64
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests


class RemotePublishError(Exception):
    """The hosted publish service could not be reached or refused the upload."""


_LOCAL_ENV = Path(__file__).resolve().parent.parent / 'publish_service' / '.env.publish'


def _local_settings() -> dict:
    """Read KEY=VALUE lines from the git-ignored publish_service/.env.publish, if present."""
    values = {}
    try:
        for line in _LOCAL_ENV.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                values[key.strip()] = value.strip().strip('"\'')
    except OSError:
        pass
    return values


def remote_config() -> Optional[Tuple[str, str]]:
    """Environment variables win; the local .env.publish file is the fallback."""
    local = _local_settings()
    url = (os.getenv('SENSAI_PUBLISH_URL') or local.get('SENSAI_PUBLISH_URL') or '').strip().rstrip('/')
    token = (os.getenv('SENSAI_PUBLISH_TOKEN') or local.get('SENSAI_PUBLISH_TOKEN') or '').strip()
    if not url or not token:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return None
    return url, token


def publish_remote(store, snapshot_id: str, timeout: float = 20.0) -> str:
    """Render the stored snapshot and upload it. Returns the public URL."""
    config = remote_config()
    if config is None:
        raise RemotePublishError('Publishing service is not configured.')
    url, token = config
    payload = {
        'id': snapshot_id,
        'html': store.export_as_html(snapshot_id, f'/p/{snapshot_id}'),
        'html_download': store.export_as_html(snapshot_id, None),
        'md': store.export_as_markdown(snapshot_id),
        'pdf_base64': base64.b64encode(store.export_as_pdf(snapshot_id)).decode('ascii'),
    }
    try:
        response = requests.post(
            url + '/api/publish',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=(5, timeout),
        )
    except requests.RequestException as error:
        raise RemotePublishError('Could not reach the publishing service. Check your internet connection.') from error
    if response.status_code == 401:
        raise RemotePublishError('The publishing service rejected the token (SENSAI_PUBLISH_TOKEN).')
    if response.status_code == 409:
        raise RemotePublishError('That snapshot id is already published.')
    if not response.ok:
        raise RemotePublishError(f'Publishing service error (HTTP {response.status_code}).')
    try:
        public_url = response.json().get('url')
    except ValueError:
        public_url = None
    if not isinstance(public_url, str) or not public_url.startswith(('http://', 'https://')):
        raise RemotePublishError('The publishing service returned an invalid link.')
    return public_url
