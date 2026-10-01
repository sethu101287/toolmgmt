"""User account storage: credentials, admin flag, chatbot access, tab/report permissions."""
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

from .appcore import TAB_DEFINITIONS, REPORT_DEFINITIONS
from .db import get_db
from . import oracle_db


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _set_tabs(conn, user_id, tabs):
    valid_tabs = [t for t in (tabs or []) if t in TAB_DEFINITIONS]
    conn.execute('DELETE FROM user_tab_access WHERE user_id = ?', (user_id,))
    for tab_key in valid_tabs:
        conn.execute('INSERT INTO user_tab_access (user_id, tab_key) VALUES (?, ?)', (user_id, tab_key))


def _set_reports(conn, user_id, reports):
    valid_reports = [r for r in (reports or []) if r in REPORT_DEFINITIONS]
    conn.execute('DELETE FROM user_report_access WHERE user_id = ?', (user_id,))
    for report_key in valid_reports:
        conn.execute('INSERT INTO user_report_access (user_id, report_key) VALUES (?, ?)', (user_id, report_key))


def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE UPPER(username) = UPPER(?)', (username,)).fetchone()


def get_user_by_id(user_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def get_user_tabs(user_id):
    with get_db() as conn:
        rows = conn.execute('SELECT tab_key FROM user_tab_access WHERE user_id = ?', (user_id,)).fetchall()
    return [row['tab_key'] for row in rows]


def get_user_reports(user_id):
    with get_db() as conn:
        rows = conn.execute('SELECT report_key FROM user_report_access WHERE user_id = ?', (user_id,)).fetchall()
    return [row['report_key'] for row in rows]


def verify_login(username, password):
    user = get_user_by_username(username)
    if not user or not user['active']:
        return None
    if not check_password_hash(user['password_hash'], password):
        return None
    return user


def list_users():
    with get_db() as conn:
        users = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
        tab_rows = conn.execute('SELECT user_id, tab_key FROM user_tab_access').fetchall()
        report_rows = conn.execute('SELECT user_id, report_key FROM user_report_access').fetchall()

    tabs_by_user = {}
    for row in tab_rows:
        tabs_by_user.setdefault(row['user_id'], []).append(row['tab_key'])

    reports_by_user = {}
    for row in report_rows:
        reports_by_user.setdefault(row['user_id'], []).append(row['report_key'])

    result = []
    for user in users:
        data = dict(user)
        data['tabs'] = tabs_by_user.get(user['id'], [])
        data['reports'] = reports_by_user.get(user['id'], [])
        result.append(data)
    return result


def create_user(username, password, is_admin, chatbot_access, tabs, reports=None):
    username = (username or '').strip()
    if not username:
        return None, 'Username is required.'
    if not password:
        return None, 'Password is required.'

    display_name = oracle_db.fetch_user_display_name(username) or username

    with get_db() as conn:
        existing = conn.execute('SELECT id FROM users WHERE UPPER(username) = UPPER(?)', (username,)).fetchone()
        if existing:
            return None, 'Username already exists.'

        cur = conn.execute(
            'INSERT INTO users (username, password_hash, is_admin, chatbot_access, active, created_at, display_name) '
            'VALUES (?, ?, ?, ?, 1, ?, ?)',
            (username, generate_password_hash(password), 1 if is_admin else 0, 1 if chatbot_access else 0, _now(),
             display_name),
        )
        user_id = cur.lastrowid
        _set_tabs(conn, user_id, tabs)
        _set_reports(conn, user_id, reports)
        conn.commit()

    return user_id, None


def update_user(user_id, is_admin, chatbot_access, tabs, active, new_password=None, reports=None):
    with get_db() as conn:
        row = conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
        if not row:
            return False, 'User not found.'

        if new_password:
            conn.execute(
                'UPDATE users SET is_admin = ?, chatbot_access = ?, active = ?, password_hash = ? WHERE id = ?',
                (1 if is_admin else 0, 1 if chatbot_access else 0, 1 if active else 0,
                 generate_password_hash(new_password), user_id),
            )
        else:
            conn.execute(
                'UPDATE users SET is_admin = ?, chatbot_access = ?, active = ? WHERE id = ?',
                (1 if is_admin else 0, 1 if chatbot_access else 0, 1 if active else 0, user_id),
            )
        _set_tabs(conn, user_id, tabs)
        _set_reports(conn, user_id, reports)
        conn.commit()

    return True, None


def delete_user(user_id):
    with get_db() as conn:
        conn.execute('DELETE FROM user_tab_access WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM user_report_access WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()


def count_admins(exclude_user_id=None):
    with get_db() as conn:
        if exclude_user_id is not None:
            row = conn.execute(
                'SELECT COUNT(*) c FROM users WHERE is_admin = 1 AND id != ?', (exclude_user_id,)
            ).fetchone()
        else:
            row = conn.execute('SELECT COUNT(*) c FROM users WHERE is_admin = 1').fetchone()
    return row['c']
