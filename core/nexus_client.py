"""HTTP client for the Nexus Repository REST API.

Uses only the Python standard library (urllib) so no new dependency is
introduced. Credentials come from environment variables (NEXUS_USERNAME /
NEXUS_PASSWORD), the same convention already used for Oracle DB access in
oracle_db.py - never entered/stored via the browser.
"""
import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_NEXUS_URL = 'https://nexus-dev.softeon.com'
DEFAULT_REPOSITORY = 'maven-releases'


def base_url():
    return (os.getenv('NEXUS_URL') or DEFAULT_NEXUS_URL).rstrip('/')


def _credentials():
    user = os.getenv('NEXUS_USERNAME')
    password = os.getenv('NEXUS_PASSWORD')
    if user and password:
        return user, password
    return None


def is_configured():
    return _credentials() is not None


def _ssl_context():
    # Corporate SSL-intercepting proxies can break verification; allow opting out via env var.
    verify = os.getenv('NEXUS_SSL_VERIFY', '1').strip().lower() not in ('0', 'false', 'no')
    if verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _build_request(url):
    req = urllib.request.Request(url)
    creds = _credentials()
    if creds:
        token = base64.b64encode(f'{creds[0]}:{creds[1]}'.encode('utf-8')).decode('ascii')
        req.add_header('Authorization', f'Basic {token}')
    return req


def _friendly_error(exc, action):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f'Nexus authentication failed ({exc.code}). Check NEXUS_USERNAME/NEXUS_PASSWORD.'
        if exc.code == 404:
            return f'Nexus {action} failed: not found (404).'
        return f'Nexus {action} failed: HTTP {exc.code}.'
    if isinstance(exc, urllib.error.URLError):
        return f'Could not reach Nexus while {action}: {exc.reason}'
    return f'Unexpected error while {action}: {exc}'


def test_connection():
    url = f'{base_url()}/service/rest/v1/status'
    try:
        with urllib.request.urlopen(_build_request(url), timeout=8, context=_ssl_context()) as resp:
            resp.read()
        return {'connected': True, 'base_url': base_url(), 'authenticated': is_configured()}
    except Exception as exc:
        return {'connected': False, 'base_url': base_url(), 'error': _friendly_error(exc, 'connecting')}


def search_components(repository=None, q=None, group=None, artifact=None, version=None, continuation_token=None):
    params = {'repository': repository or DEFAULT_REPOSITORY}
    if q:
        params['q'] = q
    if group:
        params['maven.groupId'] = group
    if artifact:
        params['maven.artifactId'] = artifact
    if version:
        params['maven.baseVersion'] = version
    if continuation_token:
        params['continuationToken'] = continuation_token

    url = f'{base_url()}/service/rest/v1/search?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(_build_request(url), timeout=15, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        return {'error': _friendly_error(exc, 'searching components')}

    rows = []
    for item in data.get('items', []):
        for asset in item.get('assets', []):
            path = asset.get('path') or ''
            rows.append({
                'group': item.get('group'),
                'artifact': item.get('name'),
                'version': item.get('version'),
                'repository': item.get('repository'),
                'format': item.get('format'),
                'path': path,
                'download_url': asset.get('downloadUrl'),
                'size': asset.get('fileSize'),
                'extension': path.rsplit('.', 1)[-1] if '.' in path else '',
            })

    return {'items': rows, 'continuation_token': data.get('continuationToken')}


def fetch_asset_bytes(download_url):
    if not download_url or not download_url.startswith(base_url()):
        return None, 'Refusing to download from a URL outside the configured Nexus server.'
    try:
        with urllib.request.urlopen(_build_request(download_url), timeout=60, context=_ssl_context()) as resp:
            return resp.read(), None
    except Exception as exc:
        return None, _friendly_error(exc, 'downloading component')
