"""Background polling of Nexus for newly published artifacts under a monitored
groupId (default com.softeon), so users can be notified without manually
Browse-ing the repository.

Runs in a daemon thread started once from app.py at process startup. Newly
detected artifacts are recorded in the nexus_artifacts table (surfaced by the
Notifications panel in the Nexus Repository screen) and optionally pushed to
Microsoft Teams / email if the corresponding environment variables are set.
"""
import json
import os
import smtplib
import threading
import time
import urllib.request
from datetime import datetime
from email.message import EmailMessage

from .db import get_db
from . import nexus_client

POLL_INTERVAL_SECONDS = int(os.getenv('NEXUS_POLL_INTERVAL_SECONDS', '300'))
MONITOR_GROUP = os.getenv('NEXUS_MONITOR_GROUP', 'com.softeon')
MONITOR_REPOSITORY = os.getenv('NEXUS_MONITOR_REPOSITORY', nexus_client.DEFAULT_REPOSITORY)

_started = False
_start_lock = threading.Lock()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _notify_teams(artifact):
    webhook = os.getenv('TEAMS_WEBHOOK_URL')
    if not webhook:
        return
    payload = {
        'text': (
            f"**New Artifact Available**\n\n"
            f"Artifact: {artifact['artifact_id']}\n"
            f"Version: {artifact['version']}\n"
            f"Published: {artifact['detected_at']}\n"
            f"Path: {artifact['path']}"
        )
    }
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Best-effort - a notification failure must never break polling.


def _notify_email(artifact):
    host = os.getenv('SMTP_HOST')
    to_addr = os.getenv('NEXUS_NOTIFY_EMAIL')
    if not host or not to_addr:
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = f"New Nexus artifact: {artifact['artifact_id']} {artifact['version']}"
        msg['From'] = os.getenv('SMTP_FROM', 'noreply@softeon.com')
        msg['To'] = to_addr
        msg.set_content(
            f"Artifact: {artifact['artifact_id']}\n"
            f"Version: {artifact['version']}\n"
            f"Published: {artifact['detected_at']}\n"
            f"Path: {artifact['path']}\n"
            f"Download: {artifact['download_url']}\n"
        )
        port = int(os.getenv('SMTP_PORT', '25'))
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            user = os.getenv('SMTP_USER')
            password = os.getenv('SMTP_PASSWORD')
            if user and password:
                smtp.starttls()
                smtp.login(user, password)
            smtp.send_message(msg)
    except Exception:
        pass


def poll_once():
    """Fetch the monitored group's artifacts and record any not seen before.

    The very first run seeds the table from whatever already exists in Nexus
    without firing outbound alerts (it's a baseline sync, not new discovery).
    Returns the list of newly recorded artifact rows (dicts)."""
    result = nexus_client.search_components(repository=MONITOR_REPOSITORY, group=MONITOR_GROUP)
    if result.get('error'):
        return []

    new_rows = []
    now = _now()
    with get_db() as conn:
        is_baseline = conn.execute('SELECT COUNT(*) c FROM nexus_artifacts').fetchone()['c'] == 0

        for item in result.get('items', []):
            exists = conn.execute(
                'SELECT id FROM nexus_artifacts WHERE group_id=? AND artifact_id=? AND version=? AND path=?',
                (item.get('group'), item.get('artifact'), item.get('version'), item.get('path')),
            ).fetchone()
            if exists:
                continue

            # Baseline sync (first-ever poll) records existing artifacts as already-seen
            # ('viewed') so the badge/toast only ever reflects genuinely new discoveries.
            initial_status = 'viewed' if is_baseline else 'new'
            cur = conn.execute(
                '''INSERT INTO nexus_artifacts
                   (group_id, artifact_id, version, repository, path, download_url, size, status, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (item.get('group'), item.get('artifact'), item.get('version'), item.get('repository'),
                 item.get('path'), item.get('download_url'), item.get('size'), initial_status, now),
            )
            new_rows.append({
                'id': cur.lastrowid,
                'group_id': item.get('group'),
                'artifact_id': item.get('artifact'),
                'version': item.get('version'),
                'repository': item.get('repository'),
                'path': item.get('path'),
                'download_url': item.get('download_url'),
                'size': item.get('size'),
                'status': initial_status,
                'detected_at': now,
            })
        conn.commit()

    if not is_baseline:
        for artifact in new_rows:
            _notify_teams(artifact)
            _notify_email(artifact)

    return new_rows


def _poll_loop():
    while True:
        try:
            poll_once()
        except Exception:
            pass  # keep the background thread alive regardless of transient errors
        time.sleep(POLL_INTERVAL_SECONDS)


def start_background_poller():
    """Start the polling thread once per process. No-op if Nexus creds aren't configured."""
    global _started
    with _start_lock:
        if _started or not nexus_client.is_configured():
            return
        _started = True
        threading.Thread(target=_poll_loop, name='nexus-poller', daemon=True).start()
