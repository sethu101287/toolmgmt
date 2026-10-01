"""Flask app instance, shared configuration and auth decorator.

Every other module imports the shared `app` object from here instead of
creating its own Flask() instance, so routes registered in the various
route modules all attach to the same application.
"""
import os
import re
from functools import wraps
from flask import Flask, redirect, url_for, session, flash

# Project root is the parent of this core/ package (where app.py, templates/, etc. live).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, root_path=BASE_DIR)
app.secret_key = os.getenv('TOOLMGMT_SECRET_KEY', os.urandom(24))

# Jinja filter so templates can check if a link is a local Windows path
app.jinja_env.filters['regex_search'] = lambda value, pattern: bool(re.match(pattern, value))

DATABASE = os.getenv('TOOLMGMT_DB_PATH', os.path.join(BASE_DIR, 'database.db'))
SHIFT_PLAN_FILE = os.path.join(BASE_DIR, 'WFH_Shift_Plan.xlsx')

DEFAULT_USERNAME = os.getenv('TOOLMGMT_DEFAULT_USERNAME', 'admin')
DEFAULT_PASSWORD = os.getenv('TOOLMGMT_DEFAULT_PASSWORD', 'admin@123')

# Dashboard tabs that access can be granted/revoked for per user (order = display order).
TAB_DEFINITIONS = {
    'links': 'Links',
    'test_deployment': 'Test Deployment',
    'auto_patch': 'Auto Patch',
    'vc_shift': 'VC Shift',
    'task_management': 'Task Management',
    'reports': 'Reports',
    'nexus_repo': 'Nexus Repository',
    'instance_details': 'Instance Details',
}

# Individual reports under the Reports tab that access can be granted/revoked per user.
REPORT_DEFINITIONS = {
    'test_status': 'QA Test Status',
    'product_ytt_wip': 'Product YTT WIP',
    'impl_ytt_wip': 'IMPL YTT WIP',
}

ORACLE_DEFAULT_USER = 'sim_admin'
ORACLE_DEFAULT_HOST = '192.1.2.45'
ORACLE_DEFAULT_PORT = '1521'
ORACLE_DEFAULT_SERVICE = 'ptsdbin'

STAGE_NAME_TO_CODE = {
    'INDIA': '5',
    'VA': '10',
    'DEV-UAT': '15',
    'UAT': '20',
    'UAT2': '21',
    'TRAINING': '22',
    'STAGE': '25',
    'PILOT': '30',
    'HD2': '35',
    'PROD': '40',
    'QA': '45',
    'PRE-PROD': '50',
    'VALIDATION': '55',
    'TEST': '70',
    'DEV_UAT02': '75',
    'TEST02': '80',
    'PFIX': '85',
    'INDIA90': '90',
}


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)

    return wrapped


def tab_required(tab_key):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            if not session.get('is_admin') and tab_key not in session.get('tabs', []):
                flash('You do not have access to that section.', 'danger')
                return redirect(url_for('dashboard'))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def chatbot_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin') and not session.get('chatbot_access'):
            flash('You do not have chatbot access.', 'danger')
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)

    return wrapped


def report_required(report_key):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            if not session.get('is_admin') and report_key not in session.get('reports', []):
                flash('You do not have access to that report.', 'danger')
                return redirect(url_for('dashboard'))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
