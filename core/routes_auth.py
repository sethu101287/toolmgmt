"""Login / logout routes."""
from flask import render_template, request, redirect, url_for, session, flash

from .appcore import app
from .users import verify_login, get_user_tabs, get_user_reports


@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        user = verify_login(username, password)
        if user:
            session['user'] = user['username']
            session['user_id'] = user['id']
            session['display_name'] = user['display_name'] or user['username']
            session['is_admin'] = bool(user['is_admin'])
            session['chatbot_access'] = bool(user['chatbot_access'])
            session['tabs'] = get_user_tabs(user['id'])
            session['reports'] = get_user_reports(user['id'])
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))
