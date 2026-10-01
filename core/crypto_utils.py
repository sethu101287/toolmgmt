"""Symmetric encryption for sensitive fields stored in the local sqlite DB
(currently: Instance Details DB passwords). Key is generated on first use and
kept out of source control (toolmgmt/data/, gitignored) - never hardcoded.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

from .appcore import BASE_DIR

_KEY_FILE = os.path.join(BASE_DIR, 'data', 'instance_secret.key')


def _get_fernet():
    os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
    if not os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, 'wb') as f:
            f.write(Fernet.generate_key())
    with open(_KEY_FILE, 'rb') as f:
        return Fernet(f.read())


def encrypt(value):
    if not value:
        return ''
    return _get_fernet().encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt(value):
    if not value:
        return ''
    try:
        return _get_fernet().decrypt(value.encode('utf-8')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''
