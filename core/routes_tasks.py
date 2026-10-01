"""Task Management tab: CRUD, assignment, comments, search/filter, KPIs, export."""
import io
import os
from datetime import date, datetime, timedelta

from flask import render_template, request, jsonify, session, send_file
from openpyxl import Workbook

from .appcore import app, login_required, tab_required, BASE_DIR
from .db import get_db
from .excel_utils import finalize_worksheet

PRIORITIES = ('High', 'Medium', 'Low')
STATUSES = ('Pending', 'In Progress', 'Completed')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _current_user():
    return session.get('user', 'unknown')


def _log_audit(conn, task_id, action, details, performed_by, performed_at):
    conn.execute(
        'INSERT INTO task_audit (task_id, action, details, performed_by, performed_at) VALUES (?, ?, ?, ?, ?)',
        (task_id, action, details, performed_by, performed_at),
    )


def _task_to_dict(row, today_str):
    data = dict(row)
    due_date = data.get('due_date')
    status = data.get('status')
    data['is_overdue'] = bool(due_date and status != 'Completed' and due_date < today_str)
    data['is_due_soon'] = bool(
        due_date and status != 'Completed' and not data['is_overdue']
        and due_date <= (date.today() + timedelta(days=1)).isoformat()
    )
    return data


@app.route('/tasks')
@login_required
@tab_required('task_management')
def task_management():
    embedded = request.args.get('embedded') == '1'
    return render_template('task_management.html', embedded=embedded)


@app.route('/tasks/alarm-sound')
@login_required
@tab_required('task_management')
def task_alarm_sound():
    return send_file(os.path.join(BASE_DIR, 'core', 'alarmsound.wav'), mimetype='audio/wav')


def _build_task_where(query_text, assignee, filter_type):
    """Build WHERE clauses/params shared by the task list and pending-by-date endpoints."""
    where = []
    params = []

    if filter_type == 'assigned':
        where.append("assigned_to IS NOT NULL AND TRIM(assigned_to) != ''")
    elif filter_type == 'unassigned':
        where.append("(assigned_to IS NULL OR TRIM(assigned_to) = '')")

    if assignee:
        where.append('assigned_to = ?')
        params.append(assignee)

    if query_text:
        like_val = f'%{query_text}%'
        where.append('''(
            LOWER(task_name) LIKE ?
            OR LOWER(COALESCE(description, '')) LIKE ?
            OR LOWER(COALESCE(assigned_to, '')) LIKE ?
            OR CAST(id AS TEXT) LIKE ?
            OR id IN (SELECT task_id FROM task_comments WHERE LOWER(comment) LIKE ?)
        )''')
        params.extend([like_val, like_val, like_val, like_val, like_val])

    return where, params


@app.route('/api/tasks', methods=['GET'])
@login_required
@tab_required('task_management')
def api_list_tasks():
    scope = request.args.get('scope', 'pending')
    query_text = (request.args.get('q') or '').strip().lower()
    assignee = (request.args.get('assignee') or '').strip()
    filter_type = (request.args.get('filter') or '').strip().lower()
    date_str = (request.args.get('date') or '').strip()

    where, params = _build_task_where(query_text, assignee, filter_type)

    if scope == 'pending':
        where.insert(0, "status != 'Completed'")
    elif scope == 'completed':
        where.insert(0, "status = 'Completed'")

    if date_str:
        if scope == 'completed':
            where.append("substr(completed_at, 1, 10) = ?")
        else:
            where.append('due_date = ?')
        params.append(date_str)

    sql = '''
        SELECT tasks.*,
               (SELECT comment FROM task_comments c WHERE c.task_id = tasks.id ORDER BY c.id DESC LIMIT 1) AS latest_comment
        FROM tasks
    '''
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY ' + ('completed_at DESC' if scope == 'completed' else 'id ASC')

    today_str = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    tasks = [_task_to_dict(row, today_str) for row in rows]
    return jsonify({'tasks': tasks, 'count': len(tasks)})


@app.route('/api/tasks/pending-by-date', methods=['GET'])
@login_required
@tab_required('task_management')
def api_pending_tasks_by_date():
    """Return tasks for the selected date per the Past/Today/Future view business rules.
       Completed tasks are always excluded here - once a task's status is Completed it
       belongs in the Completed Tasks grid, never in Pending Tasks.
       Past (selected < today): DueDate <= SelectedDate.
       Today (selected == today): DueDate <= Today (includes carried-forward tasks).
       Future (selected > today): DueDate = SelectedDate only (no carry-forward)."""
    today_str = date.today().isoformat()
    date_str = (request.args.get('date') or '').strip() or today_str
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        date_str = today_str

    query_text = (request.args.get('q') or '').strip().lower()
    assignee = (request.args.get('assignee') or '').strip()
    filter_type = (request.args.get('filter') or '').strip().lower()

    where, params = _build_task_where(query_text, assignee, filter_type)
    where.append("status != 'Completed'")

    if date_str < today_str:
        view = 'past'
        where.append('due_date IS NOT NULL AND due_date <= ?')
        params.append(date_str)
    elif date_str > today_str:
        view = 'future'
        where.append('due_date = ?')
        params.append(date_str)
    else:
        view = 'today'
        where.append('due_date IS NOT NULL AND due_date <= ?')
        params.append(today_str)

    sql = '''
        SELECT tasks.*,
               (SELECT comment FROM task_comments c WHERE c.task_id = tasks.id ORDER BY c.id DESC LIMIT 1) AS latest_comment
        FROM tasks
    '''
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY due_date ASC, id ASC'

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    tasks = [_task_to_dict(row, today_str) for row in rows]

    return jsonify({
        'selected_date': date_str,
        'view': view,
        'tasks': tasks,
        'count': len(tasks),
    })


@app.route('/api/tasks/<int:task_id>', methods=['GET'])
@login_required
@tab_required('task_management')
def api_task_detail(task_id):
    with get_db() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not task:
            return jsonify({'error': 'Task not found.'}), 404

        comments = conn.execute(
            'SELECT * FROM task_comments WHERE task_id = ? ORDER BY id DESC', (task_id,)
        ).fetchall()
        assignments = conn.execute(
            'SELECT * FROM task_assignments WHERE task_id = ? ORDER BY id DESC', (task_id,)
        ).fetchall()
        audit = conn.execute(
            'SELECT * FROM task_audit WHERE task_id = ? ORDER BY id DESC', (task_id,)
        ).fetchall()

    today_str = date.today().isoformat()
    return jsonify({
        'task': _task_to_dict(task, today_str),
        'comments': [dict(row) for row in comments],
        'assignments': [dict(row) for row in assignments],
        'audit': [dict(row) for row in audit],
    })


@app.route('/api/tasks', methods=['POST'])
@login_required
@tab_required('task_management')
def api_create_task():
    data = request.get_json(silent=True) or request.form
    task_name = (data.get('task_name') or '').strip()
    description = (data.get('description') or '').strip()
    priority = data.get('priority') or 'Medium'
    due_date = (data.get('due_date') or '').strip() or date.today().isoformat()
    alarm_enabled = 1 if str(data.get('alarm_enabled')).lower() in ('1', 'true', 'on', 'yes') else 0
    alarm_time = (data.get('alarm_time') or '').strip() or None
    if not alarm_enabled:
        alarm_time = None

    if not task_name:
        return jsonify({'error': 'Task Name is required.'}), 400
    if priority not in PRIORITIES:
        priority = 'Medium'

    created_by = _current_user()
    now = _now()

    with get_db() as conn:
        cur = conn.execute(
            '''INSERT INTO tasks (task_name, description, priority, status, created_by, created_at, due_date, alarm_enabled, alarm_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (task_name, description, priority, 'Pending', created_by, now, due_date, alarm_enabled, alarm_time),
        )
        task_id = cur.lastrowid
        _log_audit(conn, task_id, 'CREATED', f'Task "{task_name}" created.', created_by, now)
        conn.commit()

    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
@login_required
@tab_required('task_management')
def api_update_task(task_id):
    data = request.get_json(silent=True) or request.form
    performed_by = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        task_name = (data.get('task_name') or row['task_name']).strip()
        description = data.get('description', row['description'])
        priority = data.get('priority') or row['priority']
        if priority not in PRIORITIES:
            priority = row['priority']
        due_date = (data.get('due_date') if 'due_date' in data else row['due_date']) or None
        alarm_enabled = row['alarm_enabled']
        alarm_time = row['alarm_time']
        if 'alarm_enabled' in data:
            alarm_enabled = 1 if str(data.get('alarm_enabled')).lower() in ('1', 'true', 'on', 'yes') else 0
            alarm_time = (data.get('alarm_time') or '').strip() or None if alarm_enabled else None

        conn.execute(
            'UPDATE tasks SET task_name = ?, description = ?, priority = ?, due_date = ?, alarm_enabled = ?, alarm_time = ? WHERE id = ?',
            (task_name, description, priority, due_date, alarm_enabled, alarm_time, task_id),
        )
        _log_audit(conn, task_id, 'UPDATED', 'Task details updated.', performed_by, now)
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@tab_required('task_management')
def api_delete_task(task_id):
    performed_by = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT task_name FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        # Log before deleting so the audit trail records the deletion event.
        _log_audit(conn, task_id, 'DELETED', f'Task "{row["task_name"]}" deleted.', performed_by, now)
        conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/<int:task_id>/assign', methods=['POST'])
@login_required
@tab_required('task_management')
def api_assign_task(task_id):
    data = request.get_json(silent=True) or request.form
    assigned_to = (data.get('assigned_to') or '').strip()
    if not assigned_to:
        return jsonify({'error': 'Assigned Person Name is required.'}), 400

    performed_by = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT status, assigned_to FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        action = 'REASSIGNED' if (row['assigned_to'] or '').strip() else 'ASSIGNED'
        new_status = 'In Progress' if row['status'] == 'Pending' else row['status']

        conn.execute(
            'UPDATE tasks SET assigned_to = ?, assigned_at = ?, status = ? WHERE id = ?',
            (assigned_to, now, new_status, task_id),
        )
        conn.execute(
            'INSERT INTO task_assignments (task_id, assigned_to, assigned_by, assigned_at) VALUES (?, ?, ?, ?)',
            (task_id, assigned_to, performed_by, now),
        )
        _log_audit(conn, task_id, action, f'Assigned to {assigned_to}.', performed_by, now)
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/<int:task_id>/complete', methods=['POST'])
@login_required
@tab_required('task_management')
def api_complete_task(task_id):
    performed_by = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT id FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        conn.execute('UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?', ('Completed', now, task_id))
        _log_audit(conn, task_id, 'COMPLETED', 'Task marked complete.', performed_by, now)
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/complete-bulk', methods=['POST'])
@login_required
@tab_required('task_management')
def api_complete_tasks_bulk():
    body = request.get_json(silent=True) or {}
    task_ids = body.get('task_ids') or []
    try:
        task_ids = [int(t) for t in task_ids]
    except (TypeError, ValueError):
        return jsonify({'error': 'task_ids must be a list of integers.'}), 400
    if not task_ids:
        return jsonify({'error': 'No tasks selected.'}), 400

    performed_by = _current_user()
    now = _now()
    completed_ids = []

    with get_db() as conn:
        for task_id in task_ids:
            row = conn.execute("SELECT id FROM tasks WHERE id = ? AND status != 'Completed'", (task_id,)).fetchone()
            if not row:
                continue
            conn.execute('UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?', ('Completed', now, task_id))
            _log_audit(conn, task_id, 'COMPLETED', 'Task marked complete.', performed_by, now)
            completed_ids.append(task_id)
        conn.commit()

    return jsonify({'success': True, 'completed_ids': completed_ids, 'count': len(completed_ids)})


@app.route('/api/tasks/<int:task_id>/reopen', methods=['POST'])
@login_required
@tab_required('task_management')
def api_reopen_task(task_id):
    performed_by = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT id FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        conn.execute('UPDATE tasks SET status = ?, completed_at = NULL WHERE id = ?', ('Pending', task_id))
        _log_audit(conn, task_id, 'REOPENED', 'Task reopened.', performed_by, now)
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/<int:task_id>/comments', methods=['POST'])
@login_required
@tab_required('task_management')
def api_add_comment(task_id):
    data = request.get_json(silent=True) or request.form
    comment = (data.get('comment') or '').strip()
    if not comment:
        return jsonify({'error': 'Comment text is required.'}), 400

    user_name = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute('SELECT id FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Task not found.'}), 404

        conn.execute(
            'INSERT INTO task_comments (task_id, comment, user_name, created_at) VALUES (?, ?, ?, ?)',
            (task_id, comment, user_name, now),
        )
        _log_audit(conn, task_id, 'COMMENT_ADDED', comment[:200], user_name, now)
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/<int:task_id>/comments/<int:comment_id>', methods=['PUT'])
@login_required
@tab_required('task_management')
def api_edit_comment(task_id, comment_id):
    data = request.get_json(silent=True) or request.form
    new_comment = (data.get('comment') or '').strip()
    if not new_comment:
        return jsonify({'error': 'Comment text is required.'}), 400

    user_name = _current_user()
    now = _now()

    with get_db() as conn:
        row = conn.execute(
            'SELECT comment FROM task_comments WHERE id = ? AND task_id = ?', (comment_id, task_id)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Comment not found.'}), 404

        old_comment = row['comment']
        conn.execute('UPDATE task_comments SET comment = ?, edited_at = ? WHERE id = ?', (new_comment, now, comment_id))
        _log_audit(
            conn, task_id, 'COMMENT_EDITED',
            f'Comment edited. Old: "{old_comment}" New: "{new_comment}"', user_name, now,
        )
        conn.commit()

    return jsonify({'success': True})


@app.route('/api/tasks/future-summary', methods=['GET'])
@login_required
@tab_required('task_management')
def api_future_task_summary():
    """Counts of not-yet-completed tasks grouped by due date, for dates strictly after today."""
    today_str = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT due_date, COUNT(*) c FROM tasks
               WHERE due_date IS NOT NULL AND due_date > ? AND status != 'Completed'
               GROUP BY due_date ORDER BY due_date ASC""",
            (today_str,),
        ).fetchall()

    return jsonify({'future': [{'due_date': row['due_date'], 'count': row['c']} for row in rows]})


@app.route('/api/tasks/employees', methods=['GET'])
@login_required
@tab_required('task_management')
def api_task_employees():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT assigned_to FROM tasks WHERE assigned_to IS NOT NULL AND TRIM(assigned_to) != '' ORDER BY assigned_to"
        ).fetchall()
    return jsonify({'employees': [row['assigned_to'] for row in rows]})


@app.route('/api/tasks/notifications', methods=['GET'])
@login_required
@tab_required('task_management')
def api_task_notifications():
    today = date.today()
    today_str = today.isoformat()
    tomorrow_str = (today + timedelta(days=1)).isoformat()

    with get_db() as conn:
        overdue_rows = conn.execute(
            "SELECT id, task_name, due_date FROM tasks WHERE status != 'Completed' AND due_date IS NOT NULL AND due_date < ? ORDER BY due_date",
            (today_str,),
        ).fetchall()
        due_soon_rows = conn.execute(
            "SELECT id, task_name, due_date FROM tasks WHERE status != 'Completed' AND due_date IS NOT NULL AND due_date BETWEEN ? AND ? ORDER BY due_date",
            (today_str, tomorrow_str),
        ).fetchall()
        recent_rows = conn.execute(
            "SELECT task_id, action, details, performed_by, performed_at FROM task_audit "
            "WHERE action IN ('ASSIGNED', 'REASSIGNED', 'COMPLETED') ORDER BY id DESC LIMIT 10"
        ).fetchall()

    notifications = []
    for row in overdue_rows:
        notifications.append({
            'type': 'overdue',
            'message': f'Task #{row["id"]} "{row["task_name"]}" is overdue (due {row["due_date"]}).',
        })
    for row in due_soon_rows:
        notifications.append({
            'type': 'due_soon',
            'message': f'Task #{row["id"]} "{row["task_name"]}" is due within 24 hours ({row["due_date"]}).',
        })
    for row in recent_rows:
        notifications.append({
            'type': row['action'].lower(),
            'message': f'Task #{row["task_id"]}: {row["details"]} (by {row["performed_by"]} at {row["performed_at"]}).',
        })

    return jsonify({'notifications': notifications})


@app.route('/tasks/export', methods=['GET'])
@login_required
@tab_required('task_management')
def export_tasks():
    scope = request.args.get('scope', 'all')
    employee = (request.args.get('assignee') or '').strip()
    today_str = date.today().isoformat()

    where = []
    params = []
    if scope == 'pending':
        where.append("status != 'Completed'")
    elif scope == 'completed':
        where.append("status = 'Completed'")
    elif scope == 'overdue':
        where.append("status != 'Completed' AND due_date IS NOT NULL AND due_date < ?")
        params.append(today_str)
    elif scope == 'high_priority':
        where.append("priority = 'High'")
    elif scope == 'employee' and employee:
        where.append('assigned_to = ?')
        params.append(employee)

    sql = 'SELECT * FROM tasks'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id'

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = 'Tasks'
    headers = [
        'Task ID', 'Task Name', 'Description', 'Priority', 'Status', 'Created By',
        'Created At', 'Assigned To', 'Assigned At', 'Due Date', 'Completed At',
    ]
    ws.append(headers)
    for row in rows:
        ws.append([
            row['id'], row['task_name'], row['description'], row['priority'], row['status'],
            row['created_by'], row['created_at'], row['assigned_to'], row['assigned_at'],
            row['due_date'], row['completed_at'],
        ])

    finalize_worksheet(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f'tasks_{scope}_{today_str}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
