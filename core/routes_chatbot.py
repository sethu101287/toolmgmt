"""Chatbot tab routes."""
from flask import render_template, request, redirect, url_for, flash, session, jsonify

from .appcore import app, login_required, chatbot_required
from .chatbot_service import process_chat_message


@app.route('/chatbot', methods=['GET', 'POST'])
@login_required
@chatbot_required
def chatbot():
    history = session.get('chat_history', [])

    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            process_chat_message(message)
            history = session.get('chat_history', history)

    return render_template('chatbot.html', history=history)


@app.route('/chatbot/message', methods=['POST'])
@login_required
@chatbot_required
def chatbot_message():
    data = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()
    if not message:
        return jsonify({'ok': False, 'error': 'Message is required.'}), 400

    reply, _ = process_chat_message(message)
    return jsonify({'ok': True, 'reply': reply})


@app.route('/chatbot/clear', methods=['POST'])
@login_required
@chatbot_required
def clear_chatbot():
    session.pop('chat_history', None)
    session.pop('chat_instance_id', None)
    session.pop('chat_pending_instances', None)
    session.pop('chat_pending_message', None)
    flash('Chat history cleared.', 'info')
    session.pop('chat_pending_input', None)
    return redirect(url_for('chatbot'))
