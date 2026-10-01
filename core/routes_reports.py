"""Auto patch and test deployment report tabs."""
from collections import Counter

from flask import render_template, request, flash

from .appcore import app, login_required, tab_required
from .oracle_db import fetch_auto_patch_extracted, fetch_test_deployment_pending, _normalize_status_value


@app.route('/reports/auto-patch')
@login_required
@tab_required('auto_patch')
def auto_patch_report():
    embedded = request.args.get('embedded') == '1'
    result = fetch_auto_patch_extracted()
    if result.get('error'):
        flash(result['error'], 'danger')
        return render_template(
            'auto_patch_report.html',
            embedded=embedded,
            error=result['error'],
            row_count=0,
            rows=[],
            pie_labels=[],
            pie_values=[],
            scatter_points=[],
        )

    rows = result.get('rows', [])
    pie_counter = Counter()
    scatter_points = []

    for item in rows:
        instance_id = item.get('INSTANCE_ID') or 'UNKNOWN'
        patch_seq_no = item.get('PATCH_SEQ_NO')
        patch_no = item.get('PATCH_NO')
        pie_counter[instance_id] += 1

        try:
            seq_value = float(patch_seq_no)
            patch_value = float(patch_no) if patch_no is not None else None
            if patch_value is not None:
                scatter_points.append({
                    'x': seq_value,
                    'y': patch_value,
                    'instance': instance_id,
                    'patch_no': str(patch_no),
                })
        except (TypeError, ValueError):
            continue

    pie_labels = list(pie_counter.keys())
    pie_values = [pie_counter[k] for k in pie_labels]

    return render_template(
        'auto_patch_report.html',
        embedded=embedded,
        error=None,
        row_count=result.get('row_count', len(rows)),
        rows=rows,
        pie_labels=pie_labels,
        pie_values=pie_values,
        scatter_points=scatter_points,
    )


@app.route('/reports/test-deployment')
@login_required
@tab_required('test_deployment')
def test_deployment_report():
    embedded = request.args.get('embedded') == '1'
    result = fetch_test_deployment_pending()
    if result.get('error'):
        flash(result['error'], 'danger')
        return render_template(
            'test_deployment_report.html',
            embedded=embedded,
            error=result['error'],
            row_count=0,
            rows=[],
            instance_labels=[],
            instance_values=[],
            status_labels=[],
            status_values=[],
        )

    rows = result.get('rows', [])
    instance_counter = Counter()
    status_counter = Counter()
    for item in rows:
        instance_counter[item.get('INSTANCE_ID') or 'UNKNOWN'] += 1
        status_counter[_normalize_status_value(item.get('STATUS'))] += 1

    return render_template(
        'test_deployment_report.html',
        embedded=embedded,
        error=None,
        row_count=result.get('row_count', len(rows)),
        rows=rows,
        instance_labels=list(instance_counter.keys()),
        instance_values=[instance_counter[k] for k in instance_counter.keys()],
        status_labels=list(status_counter.keys()),
        status_values=[status_counter[k] for k in status_counter.keys()],
    )
