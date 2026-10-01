"""Dashboard tab: links CRUD, launching records, and VC shift widgets."""
import os
import re
import subprocess
from datetime import date, timedelta

from flask import render_template, request, redirect, url_for, flash, jsonify, session

from .appcore import app, login_required, tab_required, TAB_DEFINITIONS
from .db import get_db
from .vc_shift import fetch_vc_shift_details


def _redact_reviewer(vc_shift):
    """Reviewer Plan is admin-only; strip it before sending to non-admin sessions."""
    redacted = dict(vc_shift)
    redacted['reviewer'] = None
    redacted['reviewer_available'] = False
    return redacted


@app.route('/dashboard')
@login_required
def dashboard():
    if session.get('is_admin'):
        visible_tabs = list(TAB_DEFINITIONS.keys())
    else:
        user_tabs = session.get('tabs', [])
        visible_tabs = [key for key in TAB_DEFINITIONS if key in user_tabs]

    with get_db() as conn:
        records = conn.execute('SELECT id, name, link FROM links ORDER BY id DESC').fetchall()

    regular_records = []
    for record in records:
        record_name = (record['name'] or '').strip().upper()
        record_link = (record['link'] or '').strip().upper()
        is_auto_patch = record_name == 'AUTO_PATCH' or record_link == 'AUTO_PATCH'
        is_test_deployment = record_name == 'TEST_DEPLOYMENT' or record_link == 'TEST_DEPLOYMENT'
        if is_auto_patch or is_test_deployment:
            continue
        regular_records.append(record)

    vc_shift = fetch_vc_shift_details()
    if not session.get('is_admin'):
        vc_shift = _redact_reviewer(vc_shift)
    return render_template('dashboard.html', records=regular_records, vc_shift=vc_shift, visible_tabs=visible_tabs, active_page='dashboard')


@app.route('/api/vc-shift')
@login_required
@tab_required('vc_shift')
def api_vc_shift_by_offset():
    try:
        offset = int(request.args.get('offset', '0'))
    except ValueError:
        offset = 0
    target_date = date.today() + timedelta(days=offset)
    vc_shift = fetch_vc_shift_details(target_date=target_date)
    if not session.get('is_admin'):
        vc_shift = _redact_reviewer(vc_shift)
    return jsonify(vc_shift)


@app.route('/add', methods=['GET', 'POST'])
@login_required
@tab_required('links')
def add_record():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        link = request.form.get('link', '').strip()

        if not name or not link:
            flash('Both Name and Link are required.', 'danger')
            return render_template('add_record.html')

        with get_db() as conn:
            conn.execute('INSERT INTO links (name, link) VALUES (?, ?)', (name, link))
            conn.commit()

        flash(f'Record "{name}" added successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_record.html')


@app.route('/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
@tab_required('links')
def edit_record(record_id):
    with get_db() as conn:
        row = conn.execute('SELECT id, name, link FROM links WHERE id = ?', (record_id,)).fetchone()
        if not row:
            flash('Record not found.', 'danger')
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            link = request.form.get('link', '').strip()
            if not name or not link:
                flash('Both Name and Link are required.', 'danger')
                return render_template('edit_record.html', record=row)
            conn.execute('UPDATE links SET name = ?, link = ? WHERE id = ?', (name, link, record_id))
            conn.commit()
            flash(f'Record "{name}" updated successfully!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('edit_record.html', record=row)


@app.route('/run/<int:record_id>')
@login_required
@tab_required('links')
def run_record(record_id):
    """Execute a local file path or route special records to reports."""
    with get_db() as conn:
        row = conn.execute('SELECT name, link FROM links WHERE id = ?', (record_id,)).fetchone()

    if not row:
        flash('Record not found.', 'danger')
        return redirect(url_for('dashboard'))

    record_name = (row['name'] or '').strip().upper()
    record_link = (row['link'] or '').strip().upper()
    if record_name == 'AUTO_PATCH' or record_link == 'AUTO_PATCH':
        return redirect(url_for('auto_patch_report'))
    if record_name == 'TEST_DEPLOYMENT' or record_link == 'TEST_DEPLOYMENT':
        return redirect(url_for('test_deployment_report'))

    path = row['link']
    # Only allow absolute Windows paths to prevent arbitrary command injection
    if not re.match(r'^[A-Za-z]:\\', path):
        flash('This link is not a local file path.', 'danger')
        return redirect(url_for('dashboard'))

    if not os.path.isfile(path):
        flash(f'File not found: {path}', 'danger')
        return redirect(url_for('dashboard'))

    # Use cmd /c start so Windows picks the right handler (e.g. wscript for .vbs)
    subprocess.Popen(['cmd', '/c', 'start', '', path], shell=False)
    flash(f'"{row["name"]}" launched successfully.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/delete/<int:record_id>', methods=['POST'])
@login_required
@tab_required('links')
def delete_record(record_id):
    with get_db() as conn:
        row = conn.execute('SELECT name FROM links WHERE id = ?', (record_id,)).fetchone()
        if row:
            conn.execute('DELETE FROM links WHERE id = ?', (record_id,))
            conn.commit()
            flash(f'Record "{row["name"]}" deleted successfully!', 'success')
        else:
            flash('Record not found.', 'danger')

    return redirect(url_for('dashboard'))
