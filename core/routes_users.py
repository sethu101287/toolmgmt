"""Admin-only user management: create accounts and assign tab/chatbot/report access."""
from flask import render_template, request, redirect, url_for, flash, session

from .appcore import app, admin_required, TAB_DEFINITIONS, REPORT_DEFINITIONS
from .users import list_users, create_user, update_user, delete_user, count_admins


@app.route('/admin/users')
@admin_required
def manage_users():
    return render_template(
        'admin_users.html',
        users=list_users(),
        tab_definitions=TAB_DEFINITIONS,
        report_definitions=REPORT_DEFINITIONS,
        active_page='manage_users',
    )


@app.route('/admin/users/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    is_admin = request.form.get('is_admin') == 'on'
    chatbot_access = request.form.get('chatbot_access') == 'on'
    tabs = request.form.getlist('tabs')
    reports = request.form.getlist('reports')

    if password != confirm_password:
        flash('Password and Confirm Password do not match.', 'danger')
        return redirect(url_for('manage_users'))

    user_id, error = create_user(username, password, is_admin, chatbot_access, tabs, reports)
    if error:
        flash(error, 'danger')
    else:
        flash(f'User "{username}" created successfully!', 'success')

    return redirect(url_for('manage_users'))


@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@admin_required
def update_user_route(user_id):
    is_admin = request.form.get('is_admin') == 'on'
    chatbot_access = request.form.get('chatbot_access') == 'on'
    active = request.form.get('active') == 'on'
    tabs = request.form.getlist('tabs')
    reports = request.form.getlist('reports')
    new_password = request.form.get('new_password', '').strip()

    # Prevent removing the last remaining admin account.
    if not is_admin and count_admins(exclude_user_id=user_id) == 0:
        flash('At least one admin user must remain.', 'danger')
        return redirect(url_for('manage_users'))

    if not active and user_id == session.get('user_id'):
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('manage_users'))

    success, error = update_user(user_id, is_admin, chatbot_access, tabs, active, new_password or None, reports)
    if error:
        flash(error, 'danger')
    else:
        flash('User updated successfully!', 'success')

    return redirect(url_for('manage_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user_route(user_id):
    if user_id == session.get('user_id'):
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('manage_users'))

    if count_admins(exclude_user_id=user_id) == 0:
        flash('At least one admin user must remain.', 'danger')
        return redirect(url_for('manage_users'))

    delete_user(user_id)
    flash('User deleted successfully!', 'success')
    return redirect(url_for('manage_users'))
