"""SQLite storage for the dashboard links table."""
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

from .appcore import DATABASE, DEFAULT_USERNAME, DEFAULT_PASSWORD, TAB_DEFINITIONS, REPORT_DEFINITIONS


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS links (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT    NOT NULL,
                link TEXT    NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name     TEXT    NOT NULL,
                description   TEXT,
                priority      TEXT    NOT NULL DEFAULT 'Medium',
                status        TEXT    NOT NULL DEFAULT 'Pending',
                created_by    TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                assigned_to   TEXT,
                assigned_at   TEXT,
                due_date      TEXT,
                completed_at  TEXT,
                alarm_enabled INTEGER NOT NULL DEFAULT 0,
                alarm_time    TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS task_assignments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      INTEGER NOT NULL,
                assigned_to  TEXT    NOT NULL,
                assigned_by  TEXT    NOT NULL,
                assigned_at  TEXT    NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS task_comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL,
                comment    TEXT    NOT NULL,
                user_name  TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                edited_at  TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS task_audit (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       INTEGER NOT NULL,
                action        TEXT    NOT NULL,
                details       TEXT,
                performed_by  TEXT    NOT NULL,
                performed_at  TEXT    NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                username       TEXT    NOT NULL UNIQUE,
                password_hash  TEXT    NOT NULL,
                is_admin       INTEGER NOT NULL DEFAULT 0,
                chatbot_access INTEGER NOT NULL DEFAULT 0,
                active         INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT    NOT NULL,
                display_name   TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_tab_access (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tab_key TEXT    NOT NULL,
                UNIQUE(user_id, tab_key)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_report_access (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                report_key TEXT    NOT NULL,
                UNIQUE(user_id, report_key)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS nexus_artifacts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id      TEXT,
                artifact_id   TEXT,
                version       TEXT,
                repository    TEXT,
                path          TEXT,
                download_url  TEXT,
                size          INTEGER,
                status        TEXT    NOT NULL DEFAULT 'new',
                detected_at   TEXT    NOT NULL,
                viewed_at     TEXT,
                downloaded_at TEXT,
                UNIQUE(group_id, artifact_id, version, path)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS instance_details (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id    TEXT    NOT NULL,
                environment    TEXT    NOT NULL DEFAULT '',
                web_url        TEXT,
                rf_url         TEXT,
                db_type        TEXT    NOT NULL DEFAULT '',
                db_username    TEXT,
                db_password_enc TEXT,
                db_host        TEXT,
                db_port        TEXT,
                db_sid         TEXT,
                db_name        TEXT,
                db_server_name TEXT,
                log_path       TEXT,
                daemon_path    TEXT,
                created_by     TEXT,
                created_date   TEXT,
                updated_by     TEXT,
                updated_date   TEXT,
                UNIQUE(instance_id, environment, db_type)
            )
        ''')
        # Migrate databases created before Instance Details supported per-environment/
        # per-db-type records (old schema had UNIQUE(instance_id) alone, one row per instance).
        existing_instance_cols = {row['name'] for row in conn.execute('PRAGMA table_info(instance_details)').fetchall()}
        if existing_instance_cols and 'db_username' not in existing_instance_cols:
            conn.execute('ALTER TABLE instance_details RENAME TO instance_details_old_single_key')
            conn.execute('''
                CREATE TABLE instance_details (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id    TEXT    NOT NULL,
                    environment    TEXT    NOT NULL DEFAULT '',
                    web_url        TEXT,
                    rf_url         TEXT,
                    db_type        TEXT    NOT NULL DEFAULT '',
                    db_username    TEXT,
                    db_password_enc TEXT,
                    db_host        TEXT,
                    db_port        TEXT,
                    db_sid         TEXT,
                    db_name        TEXT,
                    db_server_name TEXT,
                    log_path       TEXT,
                    daemon_path    TEXT,
                    created_by     TEXT,
                    created_date   TEXT,
                    updated_by     TEXT,
                    updated_date   TEXT,
                    UNIQUE(instance_id, environment, db_type)
                )
            ''')
            conn.execute('''
                INSERT OR IGNORE INTO instance_details
                    (instance_id, environment, web_url, rf_url, db_type, log_path, daemon_path,
                     created_by, created_date, updated_by, updated_date)
                SELECT instance_id, COALESCE(environment, ''), web_url, rf_url, COALESCE(db_type, ''),
                       log_path, daemon_path, created_by, created_date, updated_by, updated_date
                FROM instance_details_old_single_key
            ''')
            conn.execute('DROP TABLE instance_details_old_single_key')

        # Migrate databases created before the alarm feature existed.
        existing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(tasks)').fetchall()}
        if 'alarm_enabled' not in existing_cols:
            conn.execute('ALTER TABLE tasks ADD COLUMN alarm_enabled INTEGER NOT NULL DEFAULT 0')
        if 'alarm_time' not in existing_cols:
            conn.execute('ALTER TABLE tasks ADD COLUMN alarm_time TEXT')

        # Migrate databases created before the display_name (Login As name) feature existed.
        existing_user_cols = {row['name'] for row in conn.execute('PRAGMA table_info(users)').fetchall()}
        if 'display_name' not in existing_user_cols:
            conn.execute('ALTER TABLE users ADD COLUMN display_name TEXT')

        # Seed a default admin account (full access) the first time the app runs.
        user_count = conn.execute('SELECT COUNT(*) c FROM users').fetchone()['c']
        if user_count == 0:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cur = conn.execute(
                'INSERT INTO users (username, password_hash, is_admin, chatbot_access, active, created_at, display_name) '
                'VALUES (?, ?, 1, 1, 1, ?, ?)',
                (DEFAULT_USERNAME, generate_password_hash(DEFAULT_PASSWORD), now, DEFAULT_USERNAME),
            )
            admin_id = cur.lastrowid
            for tab_key in TAB_DEFINITIONS:
                conn.execute('INSERT INTO user_tab_access (user_id, tab_key) VALUES (?, ?)', (admin_id, tab_key))
            for report_key in REPORT_DEFINITIONS:
                conn.execute('INSERT INTO user_report_access (user_id, report_key) VALUES (?, ?)', (admin_id, report_key))

        conn.commit()

