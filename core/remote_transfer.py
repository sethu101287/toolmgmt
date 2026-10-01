"""Secure (SFTP) transfer of downloaded Nexus artifacts to the remote tools server.

Credentials come only from environment variables (REMOTE_SSH_USER plus either
REMOTE_SSH_PASSWORD or REMOTE_SSH_KEY_PATH) - never hardcoded, never entered
in the browser, never logged.
"""
import os
import socket

import paramiko

try:
    import winreg
except ImportError:
    winreg = None

REMOTE_HOST = os.getenv('REMOTE_SERVER_HOST', '192.1.2.93')
REMOTE_PORT = int(os.getenv('REMOTE_SERVER_PORT', '22'))
REMOTE_PATH = os.getenv('REMOTE_SERVER_PATH', '/u02/VC/Tools')


def _get_windows_user_env(name):
    if os.name != 'nt' or winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value.strip() if isinstance(value, str) else value
    except OSError:
        return None


def _get_setting(name):
    return os.getenv(name) or _get_windows_user_env(name)


def is_configured():
    user = _get_setting('REMOTE_SSH_USER')
    return bool(user and (_get_setting('REMOTE_SSH_PASSWORD') or _get_setting('REMOTE_SSH_KEY_PATH')))


def test_connection():
    """Check TCP reachability first, then (if credentials are configured) full SSH auth."""
    result = {
        'host': REMOTE_HOST,
        'port': REMOTE_PORT,
        'path': REMOTE_PATH,
        'configured': is_configured(),
        'reachable': False,
        'authenticated': False,
        'error': None,
    }

    try:
        with socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=6):
            result['reachable'] = True
    except Exception as exc:
        result['error'] = f'Cannot reach {REMOTE_HOST}:{REMOTE_PORT} - {exc}'
        return result

    if not result['configured']:
        result['error'] = (
            'Remote server credentials are not configured (REMOTE_SSH_USER / '
            'REMOTE_SSH_PASSWORD or REMOTE_SSH_KEY_PATH).'
        )
        return result

    try:
        client = _connect()
        client.close()
        result['authenticated'] = True
    except Exception as exc:
        result['error'] = f'Authentication failed: {exc}'

    return result


def _connect():
    user = _get_setting('REMOTE_SSH_USER')
    password = _get_setting('REMOTE_SSH_PASSWORD')
    key_path = _get_setting('REMOTE_SSH_KEY_PATH')
    if not user or not (password or key_path):
        raise RuntimeError(
            'Remote server credentials are not configured. Set REMOTE_SSH_USER and '
            'REMOTE_SSH_PASSWORD or REMOTE_SSH_KEY_PATH environment variables on the server.'
        )

    client = paramiko.SSHClient()
    known_hosts = _get_setting('REMOTE_SSH_KNOWN_HOSTS')
    if known_hosts and os.path.exists(known_hosts):
        client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # No pinned host key file provided - accept-and-remember for first connect.
        # Provide REMOTE_SSH_KNOWN_HOSTS in production to pin the host key instead.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {'hostname': REMOTE_HOST, 'port': REMOTE_PORT, 'username': user, 'timeout': 15}
    if key_path:
        connect_kwargs['key_filename'] = key_path
    if password:
        connect_kwargs['password'] = password
    client.connect(**connect_kwargs)
    return client


def _mkdir_p(sftp, remote_directory):
    dirs = []
    path = remote_directory
    while len(path) > 1:
        dirs.append(path)
        path = os.path.dirname(path)
    for directory in reversed(dirs):
        try:
            sftp.stat(directory)
        except IOError:
            sftp.mkdir(directory)


def upload_files(files):
    """files: list of (filename, bytes). Returns list of {filename, success, error}."""
    if not is_configured():
        return [{'filename': name, 'success': False, 'error':
                  'Remote server credentials are not configured (REMOTE_SSH_USER / '
                  'REMOTE_SSH_PASSWORD or REMOTE_SSH_KEY_PATH).'} for name, _ in files]

    try:
        client = _connect()
    except Exception as exc:
        return [{'filename': name, 'success': False, 'error': str(exc)} for name, _ in files]

    results = []
    try:
        sftp = client.open_sftp()
        try:
            _mkdir_p(sftp, REMOTE_PATH)
            for filename, content in files:
                remote_path = f'{REMOTE_PATH.rstrip("/")}/{filename}'
                try:
                    with sftp.open(remote_path, 'wb') as remote_file:
                        remote_file.write(content)
                    results.append({'filename': filename, 'success': True})
                except Exception as exc:
                    results.append({'filename': filename, 'success': False, 'error': str(exc)})
        finally:
            sftp.close()
    finally:
        client.close()

    return results
