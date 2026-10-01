"""Nexus Repository screen: search Maven artifacts and download selected components.

Standalone screen (not embedded in the Dashboard tabs) - reachable via its own
navbar link, gated by the 'nexus_repo' tab permission like other sections.
"""
import io
import zipfile
from datetime import datetime

from flask import render_template, request, jsonify, send_file

from .appcore import app, login_required, tab_required
from .db import get_db
from . import nexus_client
from . import remote_transfer
from . import nexus_poller


@app.route('/nexus')
@login_required
@tab_required('nexus_repo')
def nexus_repository():
    return render_template(
        'nexus_repository.html',
        default_repository=nexus_client.DEFAULT_REPOSITORY,
        nexus_base_url=nexus_client.base_url(),
        active_page='nexus_repository',
    )


@app.route('/api/nexus/status', methods=['GET'])
@login_required
@tab_required('nexus_repo')
def api_nexus_status():
    return jsonify(nexus_client.test_connection())


@app.route('/api/nexus/search', methods=['GET'])
@login_required
@tab_required('nexus_repo')
def api_nexus_search():
    result = nexus_client.search_components(
        repository=(request.args.get('repository') or '').strip() or None,
        q=(request.args.get('q') or '').strip() or None,
        group=(request.args.get('group') or '').strip() or None,
        artifact=(request.args.get('artifact') or '').strip() or None,
        version=(request.args.get('version') or '').strip() or None,
        continuation_token=(request.args.get('continuation_token') or '').strip() or None,
    )
    if result.get('error'):
        return jsonify(result), 502
    return jsonify(result)


@app.route('/api/nexus/download', methods=['POST'])
@login_required
@tab_required('nexus_repo')
def api_nexus_download():
    data = request.get_json(silent=True) or {}
    assets = data.get('assets') or []
    if not assets:
        return jsonify({'error': 'No components selected.'}), 400

    if len(assets) == 1:
        asset = assets[0]
        content, err = nexus_client.fetch_asset_bytes(asset.get('download_url'))
        if err:
            return jsonify({'error': err}), 502
        filename = (asset.get('path') or 'component').rsplit('/', 1)[-1]
        return send_file(io.BytesIO(content), as_attachment=True, download_name=filename)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for asset in assets:
            content, err = nexus_client.fetch_asset_bytes(asset.get('download_url'))
            if err:
                return jsonify({'error': f'{asset.get("path") or asset.get("download_url")}: {err}'}), 502
            arcname = (asset.get('path') or 'component').lstrip('/')
            zf.writestr(arcname, content)

    buffer.seek(0)
    zip_name = f'nexus-components-{datetime.now().strftime("%Y%m%d-%H%M%S")}.zip'
    return send_file(buffer, as_attachment=True, download_name=zip_name, mimetype='application/zip')


@app.route('/api/nexus/remote-status', methods=['GET'])
@login_required
@tab_required('nexus_repo')
def api_nexus_remote_status():
    return jsonify(remote_transfer.test_connection())


@app.route('/api/nexus/download-remote', methods=['POST'])
@login_required
@tab_required('nexus_repo')
def api_nexus_download_remote():
    data = request.get_json(silent=True) or {}
    assets = data.get('assets') or []
    if not assets:
        return jsonify({'error': 'No components selected.'}), 400

    files = []
    for asset in assets:
        content, err = nexus_client.fetch_asset_bytes(asset.get('download_url'))
        filename = (asset.get('path') or 'component').rsplit('/', 1)[-1]
        if err:
            return jsonify({'error': f'{filename}: {err}'}), 502
        files.append((filename, content))

    results = remote_transfer.upload_files(files)
    all_ok = all(r['success'] for r in results)
    return jsonify({
        'success': all_ok,
        'host': remote_transfer.REMOTE_HOST,
        'path': remote_transfer.REMOTE_PATH,
        'results': results,
    }), (200 if all_ok else 502)


def _notification_to_dict(row):
    data = dict(row)
    group_path = (data.get('group_id') or '').replace('.', '/')
    data['nexus_ui_url'] = f'{nexus_client.base_url()}/#browse/browse:{data.get("repository") or nexus_poller.MONITOR_REPOSITORY}:{group_path}'
    return data


@app.route('/api/nexus/notifications', methods=['GET'])
@login_required
@tab_required('nexus_repo')
def api_nexus_notifications():
    status = (request.args.get('status') or '').strip()
    query_text = (request.args.get('q') or '').strip().lower()
    from_date = (request.args.get('from_date') or '').strip()
    to_date = (request.args.get('to_date') or '').strip()

    where = []
    params = []
    if status:
        where.append('status = ?')
        params.append(status)
    if query_text:
        like_val = f'%{query_text}%'
        where.append('(LOWER(artifact_id) LIKE ? OR LOWER(version) LIKE ? OR LOWER(group_id) LIKE ?)')
        params.extend([like_val, like_val, like_val])
    if from_date:
        where.append('detected_at >= ?')
        params.append(from_date)
    if to_date:
        where.append('detected_at <= ?')
        params.append(to_date + ' 23:59:59')

    sql = 'SELECT * FROM nexus_artifacts'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY detected_at DESC, id DESC LIMIT 200'

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    return jsonify({'notifications': [_notification_to_dict(row) for row in rows]})


@app.route('/api/nexus/notifications/unread-count', methods=['GET'])
@login_required
@tab_required('nexus_repo')
def api_nexus_notifications_unread_count():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM nexus_artifacts WHERE status = 'new'").fetchone()['c']
    return jsonify({'count': count})


@app.route('/api/nexus/notifications/<int:notification_id>/view', methods=['POST'])
@login_required
@tab_required('nexus_repo')
def api_nexus_notification_view(notification_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE nexus_artifacts SET status = 'viewed', viewed_at = ? WHERE id = ? AND status = 'new'",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), notification_id),
        )
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/nexus/notifications/<int:notification_id>/downloaded', methods=['POST'])
@login_required
@tab_required('nexus_repo')
def api_nexus_notification_downloaded(notification_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE nexus_artifacts SET status = 'downloaded', downloaded_at = ? WHERE id = ?",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), notification_id),
        )
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/nexus/notifications/poll-now', methods=['POST'])
@login_required
@tab_required('nexus_repo')
def api_nexus_notifications_poll_now():
    new_rows = nexus_poller.poll_once()
    return jsonify({'new_count': len(new_rows), 'notifications': new_rows})
