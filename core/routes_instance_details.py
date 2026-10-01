"""Instance Details tab: centralized environment/URL/DB/path info per Instance
ID, stored independently per (Instance ID, Environment, Database Type) so
saving one environment's config never overwrites another's.

All users with the tab granted can view; only Admins can add/edit/delete
(enforced server-side via admin_required, not just hidden in the UI). DB
passwords are encrypted at rest (core/crypto_utils.py); the dedicated
/secret endpoint decrypts on demand for the reveal-password eye icon,
available to any user with the tab - the general view/list endpoints only
expose whether a password is set (has_password flag) until revealed.
"""
from datetime import datetime

from flask import render_template, request, jsonify, session

from .appcore import app, login_required, tab_required, admin_required
from .db import get_db
from . import oracle_db
from . import crypto_utils

ENVIRONMENTS = ('QA', 'UAT', 'PROD', 'Stage')
DB_TYPES = ('Oracle', 'PostgreSQL', 'SQL Server')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _current_user():
    return session.get('user', 'unknown')


def _public_row(row):
    """Row dict for the general view/list endpoints - password never included, just a flag."""
    data = dict(row)
    data['has_password'] = bool(data.pop('db_password_enc', None))
    return data


@app.route('/instance-details')
@login_required
@tab_required('instance_details')
def instance_details():
    embedded = request.args.get('embedded') == '1'
    return render_template(
        'instance_details.html', embedded=embedded, environments=ENVIRONMENTS, db_types=DB_TYPES,
    )


@app.route('/api/instance-details/instance-ids')
@login_required
@tab_required('instance_details')
def api_instance_details_instance_ids():
    result = oracle_db.fetch_active_instance_ids()
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


@app.route('/api/instance-details/lookup')
@login_required
@tab_required('instance_details')
def api_instance_details_lookup():
    """All configs saved for one Instance ID + Environment (usually 0 or 1 per
    Database Type, but the schema allows more than one Database Type per
    environment)."""
    instance_id = (request.args.get('instance_id') or '').strip()
    environment = (request.args.get('environment') or '').strip()
    if not instance_id or not environment:
        return jsonify({'success': False, 'message': 'Instance ID and Environment are required.'}), 400

    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM instance_details WHERE instance_id = ? AND environment = ? ORDER BY db_type',
            (instance_id, environment),
        ).fetchall()
    return jsonify({'success': True, 'configs': [_public_row(row) for row in rows]})


@app.route('/api/instance-details/secret')
@login_required
@tab_required('instance_details')
def api_instance_details_secret():
    """Decrypted DB password - viewable by any user with the tab (reveal-password
    eye icon); prefilling the Admin edit form also reuses this endpoint."""
    instance_id = (request.args.get('instance_id') or '').strip()
    environment = (request.args.get('environment') or '').strip()
    db_type = (request.args.get('db_type') or '').strip()
    with get_db() as conn:
        row = conn.execute(
            'SELECT db_password_enc FROM instance_details WHERE instance_id = ? AND environment = ? AND db_type = ?',
            (instance_id, environment, db_type),
        ).fetchone()
    password = crypto_utils.decrypt(row['db_password_enc']) if row and row['db_password_enc'] else ''
    return jsonify({'db_password': password})


@app.route('/api/instance-details/save', methods=['POST'])
@login_required
@admin_required
def api_instance_details_save():
    data = request.get_json(silent=True) or {}
    instance_id = (data.get('instance_id') or '').strip()
    environment = (data.get('environment') or '').strip()
    db_type = (data.get('db_type') or '').strip()

    if not instance_id:
        return jsonify({'success': False, 'message': 'Instance ID is required.'}), 400
    if environment not in ENVIRONMENTS:
        return jsonify({'success': False, 'message': f'Invalid Environment "{environment}".'}), 400
    if db_type and db_type not in DB_TYPES:
        return jsonify({'success': False, 'message': f'Invalid Database Type "{db_type}".'}), 400

    web_url = (data.get('web_url') or '').strip()
    rf_url = (data.get('rf_url') or '').strip()
    db_username = (data.get('db_username') or '').strip()
    db_password = data.get('db_password')  # None/blank => keep existing password unchanged
    db_host = (data.get('db_host') or '').strip()
    db_port = (data.get('db_port') or '').strip()
    db_sid = (data.get('db_sid') or '').strip()
    db_name = (data.get('db_name') or '').strip()
    db_server_name = (data.get('db_server_name') or '').strip()
    log_path = (data.get('log_path') or '').strip()
    daemon_path = (data.get('daemon_path') or '').strip()

    now = _now()
    user = _current_user()
    with get_db() as conn:
        existing = conn.execute(
            'SELECT id, db_password_enc FROM instance_details WHERE instance_id = ? AND environment = ? AND db_type = ?',
            (instance_id, environment, db_type),
        ).fetchone()

        password_enc = crypto_utils.encrypt(db_password) if db_password else (existing['db_password_enc'] if existing else '')

        if existing:
            conn.execute(
                'UPDATE instance_details SET web_url = ?, rf_url = ?, db_username = ?, db_password_enc = ?, '
                'db_host = ?, db_port = ?, db_sid = ?, db_name = ?, db_server_name = ?, log_path = ?, '
                'daemon_path = ?, updated_by = ?, updated_date = ? WHERE id = ?',
                (web_url, rf_url, db_username, password_enc, db_host, db_port, db_sid, db_name,
                 db_server_name, log_path, daemon_path, user, now, existing['id']),
            )
            created = False
        else:
            conn.execute(
                'INSERT INTO instance_details (instance_id, environment, web_url, rf_url, db_type, db_username, '
                'db_password_enc, db_host, db_port, db_sid, db_name, db_server_name, log_path, daemon_path, '
                'created_by, created_date, updated_by, updated_date) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (instance_id, environment, web_url, rf_url, db_type, db_username, password_enc, db_host,
                 db_port, db_sid, db_name, db_server_name, log_path, daemon_path, user, now, user, now),
            )
            created = True
        conn.commit()
        row = conn.execute(
            'SELECT * FROM instance_details WHERE instance_id = ? AND environment = ? AND db_type = ?',
            (instance_id, environment, db_type),
        ).fetchone()

    suffix = f' / {db_type}' if db_type else ''
    action = 'created' if created else 'updated'
    message = f'Configuration {action} for {instance_id} / {environment}{suffix}.'
    return jsonify({'success': True, 'created': created, 'message': message, 'data': _public_row(row)})


@app.route('/api/instance-details/delete', methods=['DELETE'])
@login_required
@admin_required
def api_instance_details_delete():
    instance_id = (request.args.get('instance_id') or '').strip()
    environment = (request.args.get('environment') or '').strip()
    db_type = (request.args.get('db_type') or '').strip()
    with get_db() as conn:
        existing = conn.execute(
            'SELECT id FROM instance_details WHERE instance_id = ? AND environment = ? AND db_type = ?',
            (instance_id, environment, db_type),
        ).fetchone()
        if not existing:
            return jsonify({'success': False, 'message': 'No matching configuration found.'}), 404
        conn.execute('DELETE FROM instance_details WHERE id = ?', (existing['id'],))
        conn.commit()
    return jsonify({'success': True})
