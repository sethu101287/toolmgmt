"""Application entry point.

Run this file only: `python app.py`. All routes and business logic live in
the core/ package (core.appcore, core.db, core.nlu, core.oracle_db,
core.vc_shift, core.chatbot_service, core.routes_*) and are wired into the
shared Flask `app` instance on import.
"""
import os

from core.appcore import app
from core import db

# Importing these registers their @app.route views on the shared app instance.
from core import routes_auth
from core import routes_records
from core import routes_reports
from core import routes_chatbot
from core import routes_tasks
from core import routes_users
from core import routes_test_status
from core import routes_nexus
from core import routes_instance_details

if __name__ == '__main__':
    db.init_db()
    debug_mode = os.getenv('FLASK_DEBUG', '0').strip() in ('1', 'true', 'TRUE', 'yes', 'YES')
    # Under the debug reloader, only the reloaded child process (WERKZEUG_RUN_MAIN=true) should poll.
    if not debug_mode or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from core import nexus_poller
        nexus_poller.start_background_poller()
    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
