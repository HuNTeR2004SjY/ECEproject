"""
=============================

Flask backend that connects the trained model to a web frontend.

Run: python app.py
Then open: http://localhost:5000 (or configured port)
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, stream_with_context

import re
import threading
from dotenv import load_dotenv

# Slack App Includes
from slack_integration import SlackIntegration
from slack_events import start_socket_mode

# Load .env file for local development
try:
    load_dotenv()
except ImportError:
    pass  # dotenv not installed — environment vars must be set manually

import sys
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

# Ensure current directory is in path for imports
sys.path.append('.')

# Import centralized configuration
import json
import config

from src.problem_solver_fixed import ProblemSolver
from src.inference_service_full import TriageSpecialist
from src.automation_specialist import AutomationSpecialist
from src.models import User, Department, Company, CompanySettings, HumanTeamMember
from src.explainable_triage import ExplainableTriageWrapper
from src.pattern_miner import PatternMiner
from src.workflow_manager import WorkflowManager
from src.jira_integration import JiraIntegration, save_jira_key, get_jira_key

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.urandom(24)

# Initialize Flask-Login
from werkzeug.middleware.proxy_fix import ProxyFix

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)



# Global variables
solver = None
automation_specialist = None
xai_wrapper = ExplainableTriageWrapper()
pattern_miner = PatternMiner(db_path=config.DATABASE_PATH)
workflow_manager = None
process_monitor = None
# ── Per-company integration factories ─────────────────────────────────────────
# Instead of global singletons, each request builds an instance loaded with
# the current company's settings from the DB.

def get_jira() -> JiraIntegration:
    """Return a JiraIntegration configured for the current user's company."""
    try:
        company_id = current_user.company_id if current_user and current_user.is_authenticated else None
        cfg = CompanySettings.get_jira_config(company_id) if company_id else config.JIRA
    except Exception:
        cfg = config.JIRA
    return JiraIntegration(jira_config=cfg)

def get_slack() -> SlackIntegration:
    """Return a SlackIntegration configured for the current user's company."""
    try:
        company_id = current_user.company_id if current_user and current_user.is_authenticated else None
        cfg = CompanySettings.get_slack_config(company_id) if company_id else config.SLACK
    except Exception:
        cfg = config.SLACK
    return SlackIntegration(slack_config=cfg)

# Keep a startup-time global slack for socket mode (uses .env defaults)
slack = SlackIntegration()

# Ensure temp directories exist
os.makedirs(os.path.join(config.BASE_DIR, 'tmp'), exist_ok=True)


class LogStatusReporter:
    """
    Simple status reporter that logs metrics to the standard logger.
    In production, this would push individual metrics to DataDog, Prometheus, etc.
    """
    def receive_metrics(self, metrics):
        # We just log a heartbeat here to avoid spamming 
        # Detailed metrics are already logged by the monitor itself in summary
        pass

def init_solver():
    """Initialize the Problem Solver agent and Automation Specialist."""
    global solver, automation_specialist, process_monitor
    
    if solver is None:
        logger.info("Initializing Problem Solver Agent...")
        # Initialize Triage Specialist first (shared component)
        triage_specialist = TriageSpecialist(db_path=config.DATABASE_PATH)
        
        # Initialize Problem Solver with config values
        solver = ProblemSolver(
            triage_specialist=triage_specialist,
            model_name=config.GENERATOR_MODEL,
            enable_web_search=config.SOLVER['enable_web_search'],
            max_attempts=config.SOLVER['max_attempts']
        )
        logger.info("Problem Solver Agent Ready!")
    
    if automation_specialist is None:
        logger.info("Initializing Automation Specialist...")
        automation_specialist = AutomationSpecialist(email_config=config.EMAIL_CONFIG)
        logger.info("Automation Specialist Ready!")

    global workflow_manager
    if workflow_manager is None:
        from src.workflow_manager import WorkflowManager
        # Uses the shared triage and solver
        workflow_manager = WorkflowManager(
            triage_specialist=solver.triage if solver else None,
            problem_solver=solver,
            automation_specialist=automation_specialist
        )

    # Initialize ProcessMonitor
    if process_monitor is None:
        try:
            from src.process_monitor import ProcessMonitor
            logger.info("Initializing Process Monitor Agent...")
            process_monitor = ProcessMonitor(
                db_path=config.DATABASE_PATH,
                status_reporter=LogStatusReporter(),
                check_interval_seconds=300
            )
            process_monitor.start()
            logger.info("Process Monitor Agent Started!")
        except Exception as e:
            logger.error(f"Failed to start Process Monitor: {e}")
            
    # Initialize DB schemas and auto-close background job
    init_db_schema()
    start_auto_close_job()

def init_db_schema():
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Create core tables that are missing
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                domain TEXT,
                email TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee',
                email TEXT,
                FOREIGN KEY (company_id) REFERENCES companies (id)
            );
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies (id)
            );
            CREATE TABLE IF NOT EXISTS classified_tickets (
                id TEXT PRIMARY KEY,
                subject TEXT,
                body TEXT,
                pred_type TEXT,
                pred_priority TEXT,
                pred_queue TEXT,
                timestamp DATETIME,
                corrected BOOLEAN DEFAULT 0,
                status TEXT,
                user_id INTEGER,
                human_agent TEXT,
                user_slack_id TEXT,
                resolution_notes TEXT,
                resolved_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS learning_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                answer TEXT,
                type TEXT,
                priority TEXT,
                queue TEXT
            );
            CREATE TABLE IF NOT EXISTS ticket_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES classified_tickets(id)
            );
        """)
        
        # Seed default data if empty (useful for fresh deployments)
        cursor.execute("SELECT COUNT(*) FROM companies")
        if cursor.fetchone()[0] == 0:
            import hashlib
            logger.info("Database is empty. Seeding default TechCorp data...")
            cursor.execute("INSERT INTO companies (name, domain, email) VALUES (?, ?, ?)", 
                           ('TechCorp', 'techcorp.com', 'admin@techcorp.com'))
            tech_id = cursor.lastrowid
            
            pwd_hash = hashlib.sha256('admin123'.encode()).hexdigest()
            cursor.execute("INSERT INTO users (company_id, username, password_hash, role, email) VALUES (?, ?, ?, ?, ?)",
                           (tech_id, 'admin', pwd_hash, 'admin', 'admin@techcorp.com'))
            
            emp_hash = hashlib.sha256('user123'.encode()).hexdigest()
            cursor.execute("INSERT INTO users (company_id, username, password_hash, role, email) VALUES (?, ?, ?, ?, ?)",
                           (tech_id, 'emp001', emp_hash, 'employee', 'employee@techcorp.com'))
            
            depts = [('IT', 'it-support@techcorp.com'), ('HR', 'hr@techcorp.com')]
            for name, email in depts:
                cursor.execute("INSERT INTO departments (company_id, name, email) VALUES (?, ?, ?)", (tech_id, name, email))
            conn.commit()
            

        # Add columns if not exist
        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN human_agent TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN user_slack_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN resolution_notes TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE companies ADD COLUMN email TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN resolved_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass
            
        # Create knowledge_base table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                solution TEXT,
                source TEXT,
                tags TEXT,
                queue TEXT,
                created_at TIMESTAMP
            )
        ''')
        
        # Create jira_keys table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jira_keys (
                ticket_id TEXT PRIMARY KEY,
                jira_key TEXT NOT NULL,
                created_at TEXT
            )
        ''')
        
        # Create audit_logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                ticket_id TEXT,
                user_id TEXT,
                detail TEXT,
                ip_address TEXT
            )
        ''')

        # Per-company integration settings (Jira, Slack, Email …)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS company_integrations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id  INTEGER NOT NULL,
                key         TEXT    NOT NULL,
                value       TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, key)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Database schema migrations completed.")
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")

def auto_close_tickets():
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        cutoff = datetime.now() - timedelta(days=3)
        cursor.execute('''
            SELECT id, user_id FROM classified_tickets 
            WHERE status IN ('solution_proposed', 'escalated_resolved') 
            AND timestamp < ?
        ''', (cutoff.isoformat(),))
        
        stale_tickets = cursor.fetchall()
        
        if stale_tickets:
            cursor.execute('''
                UPDATE classified_tickets 
                SET status = 'auto_closed', corrected = 1 
                WHERE status IN ('solution_proposed', 'escalated_resolved') 
                AND timestamp < ?
            ''', (cutoff.isoformat(),))
            conn.commit()
            
            # Send emails
            if automation_specialist is not None and hasattr(automation_specialist, 'gmail_api') and automation_specialist.gmail_api.available:
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart
                for t_id, user_id in stale_tickets:
                    try:
                        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
                        row = cursor.fetchone()
                        if row:
                            user_email = row[0]
                            msg = MIMEMultipart('alternative')
                            msg['Subject'] = f'Ticket #{t_id} Auto-Closed'
                            msg['To'] = user_email
                            msg['From'] = 'eceproject2026@gmail.com'
                            html = f"We're closing ticket #{t_id} as we haven't heard back. Reply to reopen anytime."
                            msg.attach(MIMEText(html, 'html'))
                            automation_specialist.gmail_api.send(msg)
                    except Exception as email_e:
                        logger.error(f"Failed to send auto-close email for {t_id}: {email_e}")
                        
        conn.close()
    except Exception as e:
        logger.error(f"Auto-close job failed: {e}")

def start_auto_close_job():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(func=auto_close_tickets, trigger="interval", hours=1)
        scheduler.start()
        logger.info("Auto-close scheduler started.")
    except ImportError:
        logger.warning("APScheduler not installed. Auto-close job will not run. Run 'pip install apscheduler'.")
    except Exception as e:
        logger.error(f"Failed to start APScheduler: {e}")

@app.route('/')
def index():
    """Root route - redirects based on login status."""
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

def audit_log(action, ticket_id, user_id, detail):
    """
    Writes an audit row to the database.
    action: e.g. TICKET_CREATED, TICKET_ESCALATED, etc.
    """
    try:
        ip_addr = request.remote_addr
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.execute('''
            INSERT INTO audit_logs (timestamp, action, ticket_id, user_id, detail, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), action, ticket_id, user_id, detail, ip_addr))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Audit log failed for action {action}: {e}")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        company_name = request.form.get('company')
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.authenticate(company_name, username, password)
        
        if user:
            login_user(user)
            flash('Logged in successfully.', 'success')
            
            audit_log('USER_LOGIN', None, str(user.id), f"User {username} logged in")
            
            next_page = request.args.get('next')
            if not next_page or url_parse(next_page).netloc != '':
                if user.role == 'admin':
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('index'))
            return redirect(next_page)
        else:
            flash('Invalid company, username, or password.', 'error')
    
    companies = User.get_all_companies()
    return render_template('login.html', companies=companies)

@app.route('/logout')
@login_required
def logout():
    audit_log('USER_LOGOUT', None, str(current_user.id), f"User {current_user.username} logged out")
    logout_user()
    return redirect(url_for('login'))


# ── Company API ────────────────────────────────────────────────────────────────

@app.route('/api/companies', methods=['GET'])
def get_companies():
    """Return all registered companies for the login page picker."""
    try:
        companies = Company.get_all()
        return jsonify({'companies': companies})
    except Exception as e:
        logger.error(f"Error fetching companies: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/register-company', methods=['POST'])
def register_company():
    """
    Register a new company and create its first admin account atomically.
    Body: { company_name, company_email, admin_username, admin_email, admin_password }
    The company email is the unique identifier — the same email cannot be used twice.
    """
    data = request.json or {}
    company_name   = (data.get('company_name')   or '').strip()
    company_email  = (data.get('company_email')  or '').strip().lower()
    admin_username = (data.get('admin_username') or '').strip()
    admin_email    = (data.get('admin_email')    or '').strip()
    admin_password = (data.get('admin_password') or '').strip()

    # Validation — all fields required
    if not all([company_name, company_email, admin_username, admin_email, admin_password]):
        return jsonify({'error': 'All fields are required.'}), 400

    # Basic email format check
    import re as _re
    if not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', company_email):
        return jsonify({'error': 'Company email address is not valid.'}), 400
    if not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', admin_email):
        return jsonify({'error': 'Admin email address is not valid.'}), 400

    if len(admin_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    # Check email uniqueness — company email is the unique identifier
    if Company.email_exists(company_email):
        return jsonify({'error': 'This company email is already registered. Please sign in instead.'}), 409

    # Atomic creation — company + admin in one transaction
    ok, result = Company.register_with_admin(
        company_name, company_email, admin_username, admin_password, admin_email
    )
    if not ok:
        return jsonify({'error': f'Registration failed: {result}'}), 500

    audit_log('COMPANY_REGISTERED', None, None,
              f"New company '{company_name}' ({company_email}) registered with admin '{admin_username}'")
    logger.info(f"New company registered: {company_name} ({company_email})")
    return jsonify({'success': True, 'company_name': company_name})



@app.route('/api/admin/users/bulk', methods=['POST'])
@login_required
def bulk_create_users():
    """
    Bulk-create employees from a CSV file upload.
    CSV columns (with header): username, email, password
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded.'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename.'}), 400

    import io, csv
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        reader = csv.DictReader(stream)

        # Normalise header names (lowercase + strip)
        if reader.fieldnames is None:
            return jsonify({'error': 'CSV has no headers. Expected: username, email, password'}), 400
        headers = [h.strip().lower() for h in reader.fieldnames]
        if not all(h in headers for h in ['username', 'email', 'password']):
            return jsonify({'error': 'CSV must have columns: username, email, password'}), 400

        created, failed, errors = 0, 0, []
        for i, row in enumerate(reader, start=2):  # start=2 because row 1 is header
            # Remap with normalised keys
            norm = {k.strip().lower(): (v or '').strip() for k, v in row.items()}
            uname = norm.get('username', '')
            email = norm.get('email', '')
            pwd   = norm.get('password', '')

            if not uname or not email or not pwd:
                errors.append(f'Row {i}: missing username, email, or password.')
                failed += 1
                continue
            if len(pwd) < 4:
                errors.append(f'Row {i} ({uname}): password too short (min 4 chars).')
                failed += 1
                continue

            ok = User.create_user(current_user.company_id, uname, pwd, email, role='employee')
            if ok:
                created += 1
            else:
                errors.append(f'Row {i} ({uname}): username already exists.')
                failed += 1

        audit_log('BULK_USER_UPLOAD', None, str(current_user.id),
                  f"Bulk upload: {created} created, {failed} failed")
        return jsonify({'success': True, 'created': created, 'failed': failed, 'errors': errors})

    except Exception as e:
        logger.error(f"Bulk upload error: {e}")
        return jsonify({'error': f'Failed to parse CSV: {str(e)}'}), 500


# ── Integrations Settings API ───────────────────────────────────────────────

SENSITIVE_KEYS = {'jira_api_token', 'slack_bot_token', 'slack_signing_secret', 'smtp_password'}

@app.route('/api/admin/integrations', methods=['GET'])
@login_required
def get_integrations():
    """Return all integration settings for the current company (secrets masked)."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    raw = CompanySettings.get_all(current_user.company_id)
    # Mask sensitive fields so tokens never travel to the browser in plaintext
    safe = {}
    for k, v in raw.items():
        safe[k] = '••••••••' if (k in SENSITIVE_KEYS and v) else (v or '')
    # Also flag whether each secret is set (so the UI can show a "Configured" badge)
    flags = {k: bool(raw.get(k)) for k in SENSITIVE_KEYS}
    return jsonify({'success': True, 'settings': safe, 'flags': flags})


@app.route('/api/admin/integrations', methods=['POST'])
@login_required
def save_integrations():
    """Save integration settings for the current company."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json or {}
    to_save = {}

    # ── Jira ──────────────────────────────────────────────────────────────────
    if 'jira_base_url'    in data: to_save['jira_base_url']    = data['jira_base_url'].strip()
    if 'jira_email'       in data: to_save['jira_email']       = data['jira_email'].strip()
    if 'jira_project_key' in data: to_save['jira_project_key'] = data['jira_project_key'].strip().upper()
    if 'jira_enabled'     in data: to_save['jira_enabled']     = 'true' if data['jira_enabled'] else 'false'
    # Only update token if a real value was submitted (not the masked placeholder)
    if data.get('jira_api_token') and data['jira_api_token'] != '••••••••':
        to_save['jira_api_token'] = data['jira_api_token'].strip()

    # ── Slack ─────────────────────────────────────────────────────────────────
    if 'slack_enabled'       in data: to_save['slack_enabled']       = 'true' if data['slack_enabled'] else 'false'
    if 'slack_ch_it'         in data: to_save['slack_ch_it']         = data['slack_ch_it'].strip().lstrip('#')
    if 'slack_ch_escalations'in data: to_save['slack_ch_escalations']= data['slack_ch_escalations'].strip().lstrip('#')
    if 'slack_ch_incidents'  in data: to_save['slack_ch_incidents']  = data['slack_ch_incidents'].strip().lstrip('#')
    if 'slack_ch_logs'       in data: to_save['slack_ch_logs']       = data['slack_ch_logs'].strip().lstrip('#')
    if data.get('slack_bot_token') and data['slack_bot_token'] != '••••••••':
        to_save['slack_bot_token'] = data['slack_bot_token'].strip()
    if data.get('slack_signing_secret') and data['slack_signing_secret'] != '••••••••':
        to_save['slack_signing_secret'] = data['slack_signing_secret'].strip()

    # ── Email / SMTP ─────────────────────────────────────────────────────────
    if 'email_enabled' in data: to_save['email_enabled'] = 'true' if data['email_enabled'] else 'false'
    if 'smtp_host'     in data: to_save['smtp_host']     = data['smtp_host'].strip()
    if 'smtp_port'     in data: to_save['smtp_port']     = str(int(data.get('smtp_port', 587)))
    if 'smtp_user'     in data: to_save['smtp_user']     = data['smtp_user'].strip()
    if 'from_email'    in data: to_save['from_email']    = data['from_email'].strip()
    if data.get('smtp_password') and data['smtp_password'] != '••••••••':
        to_save['smtp_password'] = data['smtp_password']

    if not to_save:
        return jsonify({'error': 'No settings provided.'}), 400

    CompanySettings.set_many(current_user.company_id, to_save)
    audit_log('INTEGRATION_SETTINGS_SAVED', None, str(current_user.id),
              f"Integration settings updated for company {current_user.company_id}")
    return jsonify({'success': True, 'saved': list(to_save.keys())})


@app.route('/api/admin/integrations/test', methods=['POST'])
@login_required
def test_integration():
    """Quick connectivity test for a given integration type."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    integration = (request.json or {}).get('integration', '')

    if integration == 'jira':
        try:
            client = get_jira()
            if not client.enabled:
                return jsonify({'success': False, 'message': 'Jira is disabled or API token is missing.'})
            # Attempt a lightweight API call
            import requests as _req
            from requests.auth import HTTPBasicAuth
            r = _req.get(f"{client.base_url}/rest/api/3/myself",
                         auth=HTTPBasicAuth(client.email, client.api_token),
                         headers={"Accept": "application/json"}, timeout=6)
            if r.ok:
                return jsonify({'success': True, 'message': f'Connected as {r.json().get("displayName","?")} ({client.base_url})'})
            return jsonify({'success': False, 'message': f'Jira returned {r.status_code}: {r.text[:120]}'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

    if integration == 'slack':
        try:
            s = get_slack()
            if not s.enabled:
                return jsonify({'success': False, 'message': 'Slack is disabled or bot token is missing.'})
            resp = s.client.auth_test()
            return jsonify({'success': True, 'message': f'Connected as @{resp["user"]} in {resp["team"]}'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

    if integration == 'email':
        try:
            import smtplib, ssl as _ssl
            cfg = CompanySettings.get_email_config(current_user.company_id)
            if not cfg.get('enabled'):
                return jsonify({'success': False, 'message': 'Email is disabled.'})
            ctx = _ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg['smtp_host'], 465, context=ctx, timeout=8) as s:
                s.login(cfg['smtp_user'], cfg['smtp_password'])
            return jsonify({'success': True, 'message': f'SMTP connection OK ({cfg["smtp_host"]})'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

    return jsonify({'error': 'Unknown integration type.'}), 400


# ── Team Members API ──────────────────────────────────────────────────────

@app.route('/api/admin/team-members', methods=['GET', 'POST', 'DELETE', 'PATCH'])
@login_required
def admin_team_members():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
        
    if request.method == 'GET':
        members = HumanTeamMember.get_all(current_user.company_id)
        counts = HumanTeamMember._open_ticket_counts(current_user.company_id)
        # Add open_tickets count to each member object for the UI
        for m in members:
            m['open_tickets'] = counts.get(m['id'], 0)
        return jsonify({'members': members})
        
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        email = data.get('email')
        role = data.get('role', 'Support Specialist')
        skills = data.get('skills', 'general')
        
        if not name or not email:
            return jsonify({'error': 'Name and email are required'}), 400
            
        success, result = HumanTeamMember.add(current_user.company_id, name, email, role, skills)
        if success:
            return jsonify({'success': True, 'message': 'Team member added', 'id': result})
        else:
            return jsonify({'error': result}), 400
            
    if request.method == 'DELETE':
        member_id = request.args.get('id')
        if not member_id:
            return jsonify({'error': 'Member ID required'}), 400
            
        success = HumanTeamMember.delete(member_id, current_user.company_id)
        if success:
            return jsonify({'success': True, 'message': 'Team member deleted'})
        else:
            return jsonify({'error': 'Failed to delete team member'}), 400

    if request.method == 'PATCH':
        data = request.json
        member_id = data.get('id')
        available = data.get('available')
        
        if member_id is None or available is None:
            return jsonify({'error': 'Member ID and availability status required'}), 400
            
        success = HumanTeamMember.update_availability(member_id, current_user.company_id, available)
        if success:
            return jsonify({'success': True, 'message': 'Availability updated'})
        else:
            return jsonify({'error': 'Failed to update availability'}), 400


@app.route('/dashboard')
@login_required
def dashboard():
    """Employee Dashboard (Main App)."""
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    
    # Fetch user's tickets
    tickets = User.get_user_tickets(current_user.id)
    return render_template('index.html', user=current_user, tickets=tickets)

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """Admin Dashboard."""
    if current_user.role != 'admin':
        flash('Access denied. Admins only.', 'error')
        return redirect(url_for('dashboard'))
    
    tickets = User.get_company_tickets(current_user.company_id)
    stats = User.get_company_stats(current_user.company_id)
    return render_template('admin_dashboard.html', user=current_user, tickets=tickets, stats=stats)


@app.route('/api/admin/users', methods=['GET', 'POST', 'DELETE'])
@login_required
def admin_users():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
        
    if request.method == 'GET':
        users = User.get_all_users(current_user.company_id)
        return jsonify({'users': users})
        
    if request.method == 'POST':
        data = request.json
        username = data.get('username')
        password = data.get('password')
        email = data.get('email')
        
        if not username or not password or not email:
            return jsonify({'error': 'Missing required fields'}), 400
            
        success = User.create_user(current_user.company_id, username, password, email)
        if success:
            return jsonify({'success': True, 'message': 'User created successfully'})
        else:
            return jsonify({'error': 'User creation failed. Username might exist.'}), 400

    if request.method == 'DELETE':
        user_id = request.args.get('id')
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
            
        success = User.delete(user_id)
        if success:
            return jsonify({'success': True, 'message': 'User deleted successfully'})
        else:
            return jsonify({'error': 'Failed to delete user'}), 400


@app.route('/api/admin/departments', methods=['GET', 'POST', 'DELETE'])
@login_required
def admin_departments():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
        
    if request.method == 'GET':
        depts = Department.get_all(current_user.company_id)
        return jsonify({'departments': depts})
        
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        email = data.get('email')
        
        if not name or not email:
            return jsonify({'error': 'Name and email are required'}), 400
            
        success = Department.add(current_user.company_id, name, email)
        if success:
            return jsonify({'success': True, 'message': 'Department added'})
        else:
            return jsonify({'error': 'Failed to add department'}), 400
            
    if request.method == 'DELETE':
        dept_id = request.args.get('id')
        if not dept_id:
            return jsonify({'error': 'Department ID required'}), 400
            
        success = Department.delete(dept_id)
        if success:
            return jsonify({'success': True, 'message': 'Department deleted'})
        else:
            return jsonify({'error': 'Failed to delete department'}), 400


@app.route('/predict', methods=['POST'])
@login_required
def predict():
    """API endpoint for predictions and solutions."""
    global solver
    
    # Ensure solver is initialized
    if solver is None:
        init_solver()
        
    data = request.json
    subject = data.get('subject', '')
    body = data.get('body', '')
    user_slack_id = data.get('user_slack_id', None)
    user_email = data.get('user_email', '')
    
    if not subject or not body:
        return jsonify({'error': 'Please provide both subject and body'}), 400
    
    try:
        # Connect to DB to get count for sequence
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Get count for sequence
        # We need a robust sequence counter. 
        # Simple approach: Count existing tickets.
        try:
            cursor.execute("SELECT COUNT(*) FROM classified_tickets")
            seq_num = cursor.fetchone()[0] + 1
        except:
            seq_num = 1
            
        conn.close()
        
        # Determine Company Abbr
        company_abbr = "UNK"
        user_id_for_ticket = "unknown"
        user_slack_id = None # Initialize user_slack_id
        
        if current_user.is_authenticated:
            # Assuming we can get company name from user -> company_id -> companies table
            # Or just use first 3 chars of user's username if company is not easily available in object
            # User object has company_id. 
            # Ideally we fetch company name. 
            # For speed, let's use a helper or just "CMP" if lazy, but requirement says "first 3 letters of company name".
            # We need to fetch company name from DB or User object.
            # User.get(id) returns User object with company_id.
            
            # Let's quickly fetch company name
            try:
                conn_c = sqlite3.connect(config.DATABASE_PATH)
                cursor_c = conn_c.cursor()
                cursor_c.execute("SELECT name FROM companies WHERE id = ?", (current_user.company_id,))
                row_c = cursor_c.fetchone()
                if row_c:
                    company_abbr = row_c[0][:3].upper()
                conn_c.close()
            except:
                pass
            
            user_id_for_ticket = str(current_user.id)
            user_slack_id = getattr(current_user, 'slack_id', None) # Assuming current_user has slack_id
        
        # Format: [CMP][UserID][SEQ]
        # Pad sequence to 4 digits? "sequence of no" -> imply just number or padded?
        # Let's pad to 4 digits for meaningful length.
        ticket_id = f"{company_abbr}{user_id_for_ticket}{seq_num:04d}"
        
        # Use the solver to process the ticket
        logger.info(f"Processing ticket {ticket_id}: {subject[:50]}...")
        result = solver.solve(
            subject=subject,
            body=body,
            ticket_id=ticket_id
        )
        
        # SAVE TICKET TO DATABASE (PERSISTENCE)
        try:
            conn = sqlite3.connect(config.DATABASE_PATH)
            cursor = conn.cursor()
            
            tags_json = json.dumps([t['tag'] for t in result['triage']['tags'][:5]])
            
            # Determine status based on solver result
            status = 'escalated' if result.get('escalated') else 'solution_proposed'
            
            cursor.execute('''
                INSERT INTO classified_tickets 
                (id, subject, body, pred_type, pred_priority, pred_queue, timestamp, corrected, user_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''', (
                ticket_id, 
                subject, 
                body, 
                result['triage']['type'], 
                result['triage']['priority'], 
                result['triage']['queue'], 
                datetime.now(),
                user_id_for_ticket if current_user.is_authenticated else None,
                status
            ))
            
            # Save the initial conversation (User Body + AI Solution)
            # 1. User Message
            cursor.execute('''
                INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp)
                VALUES (?, 'user', ?, ?)
            ''', (ticket_id, body, datetime.now()))
            
            # 2. AI Response (Solution)
            if result.get('solution'):
                cursor.execute('''
                    INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp)
                    VALUES (?, 'ai', ?, ?)
                ''', (ticket_id, result['solution'], datetime.now()))
            
            conn.commit()
            conn.close()
            logger.info(f"Ticket {ticket_id} saved to database with status: {status}")
            
            audit_log('TICKET_CREATED', ticket_id, user_id_for_ticket, f"Created with subject: {subject[:50]}")
            if result.get('escalated'):
                audit_log('TICKET_ESCALATED', ticket_id, user_id_for_ticket, f"Escalated reason: {result.get('escalation_reason')}")
            else:
                audit_log('TICKET_RESOLVED', ticket_id, user_id_for_ticket, f"Status: solution_proposed")
            
            # Notify User (Unified for both Solved and Escalated)
            try:
                # Get user email
                user_email = current_user.email if current_user.is_authenticated else "eceproject2026+unknown@gmail.com"
                
                # Construct ticket data 
                ticket_data = {
                    'id': ticket_id,
                    'subject': subject,
                    'body': body,
                    'type': result['triage']['type'],
                    'priority': result['triage']['priority'],
                    'user_id': user_id_for_ticket,
                    'company_id': current_user.company_id if current_user.is_authenticated else None,
                    'status': status
                }
                
                # Ensure initialized
                if automation_specialist is None:
                        init_solver()
                
                # Append confirmation links
                if not result.get('escalated') and result.get('solution'):
                    base_url = request.host_url.rstrip('/')
                    confirm_yes = f"{base_url}/ticket/confirm/{ticket_id}?response=yes"
                    confirm_no = f"{base_url}/ticket/confirm/{ticket_id}?response=no"
                    
                    confirmation_block = f"""
<br><hr><br>
<b>Did this resolve your issue?</b><br>
✅ Yes, close my ticket: <a href="{confirm_yes}">{confirm_yes}</a><br>
❌ No, I still need help: <a href="{confirm_no}">{confirm_no}</a>
"""
                    result['solution'] += confirmation_block
                
                # Call unified notification
                automation_specialist.notify_ticket_resolution(
                    ticket_data=ticket_data,
                    result=result,
                    user_email=user_email
                )
                
            except Exception as notify_err:
                logger.error(f"Failed to trigger notifications: {notify_err}")
                
        except Exception as db_err:
            logger.error(f"Failed to save ticket to database: {db_err}")
                
        # Structure the response for the frontend
        response = {
            'success': result['success'],
            'ticket_id': ticket_id,
            'solution': result['solution'],
            'confidence': round(result.get('confidence', 0.0) * 100, 1),
            'method': result.get('method', 'unknown'),
            'attempts': result.get('attempts', 1),
            'escalated': result.get('escalated', False),
            'escalation_reason': result.get('escalation_reason', ''),
            
            # Flatten triage info for easier frontend consumption
            'type': result['triage']['type'],
            'type_confidence': round(result['triage']['type_confidence'] * 100, 1),
            'priority': result['triage']['priority'],
            'priority_confidence': round(result['triage']['priority_confidence'] * 100, 1),
            'queue': result['triage']['queue'],
            'queue_confidence': round(result['triage']['queue_confidence'] * 100, 1),
            'tags': [t['tag'] for t in result['triage']['tags'][:5]],
            'tag_scores': [round(t['confidence'] * 100, 1) for t in result['triage']['tags'][:5]]
        }
        
        # ── Update Slack ID in db (if present) ───────────────────────────
        if user_slack_id:
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                conn.execute("UPDATE classified_tickets SET user_slack_id = ? WHERE id = ?", (user_slack_id, ticket_id))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to save user_slack_id: {e}")
                
        # ---------------------------------------------------------
        # EXPLAINABLE AI LAYER
        # ---------------------------------------------------------
        try:
            explanation = xai_wrapper.explain(
                triage_result=result['triage'],
                ticket_subject=subject,
                ticket_body=body
            )
            response['explanation'] = explanation.to_dict()
        except Exception as e:
            logger.error(f"XAI wrapper failed during /predict: {e}")
            response['explanation'] = {"error": "Explanation generation failed."}
            
        # ---------------------------------------------------------
        # REAL-TIME SYSTEMIC ALERT MINING
        # ---------------------------------------------------------
        try:
            systemic_alert = pattern_miner.mine(ticket_id, subject, body)
            if systemic_alert and not systemic_alert.already_known:
                logger.warning(f"SYSTEMIC ALERT triggered: {systemic_alert.alert_id}")
            response['systemic_alert'] = systemic_alert.to_dict() if systemic_alert else None
        except Exception as e:
            logger.error(f"Pattern Miner failed during /predict: {e}")
            response['systemic_alert'] = None

        # ── Jira Integration ────────────────────────────────────────────
        jira_key = None
        try:
            jira_key = jira_client.create_issue(
                ticket_id   = ticket_id,
                subject     = subject,
                body        = body,
                triage      = result['triage'],
                explanation = explanation.to_dict() if 'explanation' in locals() else None,
            )
            if jira_key:
                save_jira_key(config.DATABASE_PATH, ticket_id, jira_key)
                logger.info(f"Jira issue {jira_key} linked to ticket {ticket_id}")

            if jira_key and not result.get('escalated') and result.get('solution'):
                jira_client.update_issue_resolved(
                    jira_key   = jira_key,
                    solution   = result['solution'],
                    ticket_id  = ticket_id,
                    confidence = result.get('confidence', 0.0),
                )

            if jira_key and result.get('escalated'):
                jira_client.update_issue_escalated(
                    jira_key          = jira_key,
                    ticket_id         = ticket_id,
                    escalation_reason = result.get('escalation_reason', ''),
                )

            if systemic_alert and not systemic_alert.already_known:
                # Gather Jira keys for all clustered tickets
                cluster_jira_keys = [
                    k for k in [
                        get_jira_key(config.DATABASE_PATH, tid)
                        for tid in systemic_alert.cluster.ticket_ids
                    ] if k
                ]
                jira_client.create_systemic_epic(
                    alert_id   = systemic_alert.alert_id,
                    severity   = systemic_alert.severity,
                    summary    = systemic_alert.summary,
                    ticket_ids = systemic_alert.cluster.ticket_ids,
                    jira_keys  = cluster_jira_keys,
                )

        except Exception as jira_err:
            logger.error(f"Jira Integration failed during /predict: {jira_err}")
        # ── End Jira ────────────────────────────────────────────────────
        
        # ── Slack notifications ───────────────────────────────────────────
        try:
            # Notify ticket created
            slack.notify_ticket_created(
                ticket_id      = ticket_id,
                subject        = subject,
                priority       = result['triage'].get('priority', 'Medium'),
                queue          = result['triage'].get('queue', ''),
                user_email     = user_email,
                user_slack_id  = user_slack_id,
                jira_key       = jira_key,
            )
            # Notify solution or escalation
            if result.get('escalated'):
                slack.notify_escalation(
                    ticket_id         = ticket_id,
                    subject           = subject,
                    queue             = result['triage'].get('queue', ''),
                    escalation_reason = result.get('escalation_reason', ''),
                    user_email        = user_email,
                    user_slack_id     = user_slack_id,
                    jira_key          = jira_key,
                )
            elif result.get('solution'):
                slack.notify_solution_ready(
                    ticket_id     = ticket_id,
                    subject       = subject,
                    solution      = result['solution'],
                    confidence    = result.get('confidence', 0.75),
                    user_email    = user_email,
                    user_slack_id = user_slack_id,
                    jira_key      = jira_key,
                )
            # Notify systemic alert if pattern miner fired
            if systemic_alert and not systemic_alert.already_known:
                from src.jira_integration import get_jira_key
                cluster_jira_keys = [
                    k for k in [
                        get_jira_key(config.DATABASE_PATH, tid)
                        for tid in systemic_alert.cluster.ticket_ids
                    ] if k
                ]
                slack.notify_systemic_alert(
                    alert_id   = systemic_alert.alert_id,
                    severity   = systemic_alert.severity,
                    summary    = systemic_alert.summary,
                    ticket_ids = systemic_alert.cluster.ticket_ids,
                    jira_keys  = cluster_jira_keys,
                )
        except Exception as slack_err:
            logger.error(f"Slack notification block failed: {slack_err}")
        # ── End Slack ─────────────────────────────────────────────────────

        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error processing ticket: {e}", exc_info=True)


@app.route('/predict/stream', methods=['POST'])
@login_required
def predict_stream():
    """
    Streaming SSE endpoint for progressive ticket processing.
    Yields:
      1. 'triage'   event — immediately after triage (~1-2s)
      2. 'solution' event — after problem solver finishes (~10-30s)
      3. 'done'     event — signals completion
    """
    global solver, automation_specialist

    if solver is None:
        init_solver()

    data = request.json
    subject = data.get('subject', '')
    body = data.get('body', '')

    if not subject or not body:
        def error_stream():
            yield f"event: error\ndata: {json.dumps({'error': 'Please provide both subject and body'})}\n\n"
        return Response(stream_with_context(error_stream()), mimetype='text/event-stream')

    # Capture user identity before entering generator (request context not available inside)
    user_authenticated = current_user.is_authenticated
    user_company_id = current_user.company_id if user_authenticated else None
    user_id_str = str(current_user.id) if user_authenticated else "unknown"
    user_email = current_user.email if user_authenticated else "eceproject2026+unknown@gmail.com"
    user_slack_id = getattr(current_user, 'slack_id', None) if user_authenticated else None

    def generate():
        try:
            # --- Compute ticket_id ---
            company_abbr = "UNK"
            try:
                conn_c = sqlite3.connect(config.DATABASE_PATH)
                cursor_c = conn_c.cursor()
                if user_company_id:
                    cursor_c.execute("SELECT name FROM companies WHERE id = ?", (user_company_id,))
                    row_c = cursor_c.fetchone()
                    if row_c:
                        company_abbr = row_c[0][:3].upper()
                cursor_c.execute("SELECT COUNT(*) FROM classified_tickets")
                seq_num = (cursor_c.fetchone()[0] or 0) + 1
                conn_c.close()
            except Exception:
                seq_num = 1

            ticket_id = f"{company_abbr}{user_id_str}{seq_num:04d}"

            # --- STEP 1: Triage (fast ~1-2s) ---
            logger.info(f"[STREAM] Triage starting for ticket {ticket_id}")
            triage_result = solver.triage.predict(
                subject=subject,
                body=body,
                retrieve_answer=True
            )

            triage_event = {
                'ticket_id': ticket_id,
                'type': triage_result['type'],
                'type_confidence': round(triage_result['type_confidence'] * 100, 1),
                'priority': triage_result['priority'],
                'priority_confidence': round(triage_result['priority_confidence'] * 100, 1),
                'queue': triage_result['queue'],
                'queue_confidence': round(triage_result['queue_confidence'] * 100, 1),
                'tags': [t['tag'] for t in triage_result['tags'][:5]],
                'tag_scores': [round(t['confidence'] * 100, 1) for t in triage_result['tags'][:5]],
            }
            yield f"event: triage\ndata: {json.dumps(triage_event)}\n\n"
            logger.info(f"[STREAM] Triage event sent for ticket {ticket_id}")

            # --- Save initial ticket data to DB after triage ---
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                cursor = conn.cursor()
                status = 'pending_solution' # Initial status for streaming tickets
                
                cursor.execute('''
                    INSERT INTO classified_tickets
                    (id, subject, body, pred_type, pred_priority, pred_queue, timestamp, corrected, user_id, status, user_slack_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ''', (
                    ticket_id, subject, body,
                    triage_result['type'], triage_result['priority'], triage_result['queue'],
                    datetime.now(), user_id_str if user_authenticated else None, status, user_slack_id
                ))
                conn.commit()
                conn.close()
                logger.info(f"[STREAM] Initial ticket {ticket_id} saved (status: {status})")

                audit_log('TICKET_CREATED', ticket_id, user_id_str, f"Stream created with subject: {subject[:50]}")

                # Notify ticket created via Slack
                slack.notify_ticket_created(
                    ticket_id=ticket_id,
                    subject=subject,
                    priority=triage_result.get('priority', 'Medium'),
                    queue=triage_result.get('queue', ''),
                    user_slack_id=user_slack_id,
                    jira_key=None # Jira key not available yet
                )

            except Exception as db_err:
                logger.error(f"[STREAM] Initial DB save failed: {db_err}")


            # --- STEP 2: Full solve (slow, uses triage result internally) ---
            logger.info(f"[STREAM] Solver starting for ticket {ticket_id}")
            result = solver.solve(subject=subject, body=body, ticket_id=ticket_id)

            # --- Update ticket in DB with solution/escalation ---
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                cursor = conn.cursor()
                status = 'escalated' if result.get('escalated') else 'solution_proposed'

                cursor.execute('''
                    UPDATE classified_tickets
                    SET status = ?, pred_type = ?, pred_priority = ?, pred_queue = ?
                    WHERE id = ?
                ''', (
                    status, result['triage']['type'], result['triage']['priority'], result['triage']['queue'],
                    ticket_id
                ))

                # Add user message and AI response to interactions
                cursor.execute(
                    "INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'user', ?, ?)",
                    (ticket_id, body, datetime.now())
                )
                if result.get('solution'):
                    cursor.execute(
                        "INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'ai', ?, ?)",
                        (ticket_id, result['solution'], datetime.now())
                    )

                conn.commit()
                conn.close()
                logger.info(f"[STREAM] Ticket {ticket_id} updated with solution/escalation (status: {status})")

                if result.get('escalated'):
                    audit_log('TICKET_ESCALATED', ticket_id, user_id_str, f"Escalated reason: {result.get('escalation_reason')}")
                else:
                    audit_log('TICKET_RESOLVED', ticket_id, user_id_str, f"Status: solution_proposed")

                # Notifications (Email)
                try:
                    if automation_specialist is None:
                        init_solver()
                    ticket_data = {
                        'id': ticket_id, 'subject': subject, 'body': body,
                        'type': result['triage']['type'], 'priority': result['triage']['priority'],
                        'user_id': user_id_str, 'company_id': user_company_id, 'status': status
                    }
                    # Append confirmation links for email
                    if not result.get('escalated') and result.get('solution'):
                        base_url = request.host_url.rstrip('/')
                        confirm_yes = f"{base_url}/ticket/confirm/{ticket_id}?response=yes"
                        confirm_no = f"{base_url}/ticket/confirm/{ticket_id}?response=no"
                        confirmation_block = f"""
<br><hr><br>
<b>Did this resolve your issue?</b><br>
✅ Yes, close my ticket: <a href="{confirm_yes}">{confirm_yes}</a><br>
❌ No, I still need help: <a href="{confirm_no}">{confirm_no}</a>
"""
                        result['solution'] += confirmation_block

                    automation_specialist.notify_ticket_resolution(
                        ticket_data=ticket_data, result=result, user_email=user_email
                    )
                except Exception as notify_err:
                    logger.error(f"[STREAM] Email Notification failed: {notify_err}")

            except Exception as db_err:
                logger.error(f"[STREAM] DB update failed after solve: {db_err}")

            # ── Jira Integration for Stream ──────────────────────────────────
            jira_key = None
            try:
                jira_key = jira_client.create_issue(
                    ticket_id   = ticket_id,
                    subject     = subject,
                    body        = body,
                    triage      = result['triage'],
                    explanation = None, # Explanation is not generated in stream yet
                )
                if jira_key:
                    save_jira_key(config.DATABASE_PATH, ticket_id, jira_key)
                    logger.info(f"[STREAM] Jira issue {jira_key} linked to ticket {ticket_id}")

                if jira_key and not result.get('escalated') and result.get('solution'):
                    jira_client.update_issue_resolved(
                        jira_key   = jira_key,
                        solution   = result['solution'],
                        ticket_id  = ticket_id,
                        confidence = result.get('confidence', 0.0),
                    )

                if jira_key and result.get('escalated'):
                    jira_client.update_issue_escalated(
                        jira_key          = jira_key,
                        ticket_id         = ticket_id,
                        escalation_reason = result.get('escalation_reason', ''),
                    )
            except Exception as jira_err:
                logger.error(f"[STREAM] Jira integration failed: {jira_err}")
            # ── End Jira ─────────────────────────────────────────────────────

            # ── Slack notifications for solution/escalation ──────────────────
            try:
                if result.get('escalated'):
                    slack.notify_escalation(
                        ticket_id=ticket_id,
                        subject=subject,
                        queue=result['triage'].get('queue', ''),
                        escalation_reason=result.get('escalation_reason', ''),
                        user_slack_id=user_slack_id,
                        jira_key=jira_key,
                    )
                elif result.get('solution'):
                    slack.notify_solution_ready(
                        ticket_id=ticket_id,
                        subject=subject,
                        solution=result['solution'],
                        confidence=result.get('confidence', 0.75),
                        user_slack_id=user_slack_id,
                        jira_key=jira_key,
                    )
            except Exception as slack_err:
                logger.error(f"[STREAM] Slack notification for solution/escalation failed: {slack_err}")
            # ── End Slack ────────────────────────────────────────────────────

            # --- Yield solution event ---
            solution_event = {
                'ticket_id': ticket_id,
                'success': result['success'],
                'solution': result.get('solution'),
                'confidence': round(result.get('confidence', 0.0) * 100, 1),
                'method': result.get('method', 'unknown'),
                'attempts': result.get('attempts', 1),
                'escalated': result.get('escalated', False),
                'escalation_reason': result.get('escalation_reason', ''),
            }
            yield f"event: solution\ndata: {json.dumps(solution_event)}\n\n"

            yield f"event: done\ndata: {json.dumps({'ticket_id': ticket_id})}\n\n"

        except Exception as e:
            logger.error(f"[STREAM] Error: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@app.route('/api/ticket/<ticket_id>/details', methods=['GET'])
@login_required
def get_ticket_details(ticket_id):
    """
    Get full ticket details including conversation history.
    """
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Helper to check ownership (if not admin)
        if current_user.role != 'admin':
            # Check if ticket belongs to user (or their company? specific user for now)
            # We don't have user_id in classified_tickets reliable yet for legacy, but new ones have it.
            # Let's rely on User.get_user_tickets filter logic or just check if user_id matches
            # For now, let's allow read if it exists, assuming ID is hard to guess? 
            # Better: filtered query.
            cursor.execute("SELECT user_id FROM classified_tickets WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            if row and row['user_id'] and str(row['user_id']) != str(current_user.id):
                 # Strict check: if it has a user_id and it doesn't match, deny.
                 # If user_id is NULL (old tickets), allow for now or deny? Allow for demo.
                 return jsonify({'error': 'Unauthorized'}), 403
        
        # Fetch Ticket Info
        cursor.execute("SELECT * FROM classified_tickets WHERE id = ?", (ticket_id,))
        ticket = cursor.fetchone()
        if not ticket:
            conn.close()
            return jsonify({'error': 'Ticket not found'}), 404
            
        ticket_dict = dict(ticket)
        
        # Fetch Interactions
        cursor.execute("SELECT sender, message, timestamp FROM ticket_interactions WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
        interactions = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'ticket': ticket_dict,
            'interactions': interactions
        })
        
    except Exception as e:
        logger.error(f"Error fetching ticket details: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/ticket/<ticket_id>/reply', methods=['POST'])
@login_required
def reply_to_ticket(ticket_id):
    """
    Handle user reply to a ticket.
    Triggers AI to respond considering the full history.
    """
    global solver
    if solver is None:
        init_solver()
        
    try:
        data = request.json
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({'error': 'Message is required'}), 400
            
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Fetch existing interactions
        cursor.execute("SELECT sender, message FROM ticket_interactions WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
        rows = cursor.fetchall()
        history = [dict(row) for row in rows]
        
        # 2. Add current user message to local history (and DB later)
        history.append({'sender': 'user', 'message': user_message})
        
        # 3. Fetch Ticket Context (Subject/Body/Triage)
        cursor.execute("SELECT subject, body, pred_type, pred_priority, pred_queue FROM classified_tickets WHERE id = ?", (ticket_id,))
        ticket_row = cursor.fetchone()
        if not ticket_row:
            conn.close()
            return jsonify({'error': 'Ticket not found'}), 404
            
        subject = ticket_row['subject']
        original_body = ticket_row['body']
        
        conn.close() # Close for now, reopen to save
        
        # 4. Generate AI Response
        result = solver.solve(
            subject=subject,
            body=original_body, # We pass original body, but history contains the conversation
            ticket_id=ticket_id,
            conversation_history=history # PASS HISTORY!
        )
        
        # 5. Save everything
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Save User Message
        cursor.execute("INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'user', ?, ?)", 
                       (ticket_id, user_message, datetime.now()))
        
        # Save AI Response
        ai_response = result['solution']
        cursor.execute("INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'ai', ?, ?)", 
                       (ticket_id, ai_response, datetime.now()))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'reply': ai_response,
            'interactions': history + [{'sender': 'ai', 'message': ai_response}]
        })

    except Exception as e:
        logger.error(f"Error processing reply: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/model-info')
@login_required
def model_info():
    """Get model information."""
    # access inner config from triage specialist
    if solver and solver.triage:
        config = solver.triage.config
        return jsonify({
            'types': config['type_classes'],
            'priorities': config['priority_classes'],
            'queues': config['queue_classes'],
            'num_tags': config.get('num_unique_tags', 0)
        })
    return jsonify({})





def _cleanup_ticket(ticket_id):
    """
    Delete sensitive/large content from a resolved ticket to save space.
    Keeps the metadata record but removes body and interactions.
    """
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # 1. Delete interactions (chat history)
        cursor.execute("DELETE FROM ticket_interactions WHERE ticket_id = ?", (ticket_id,))
        
        # 2. Preserve body content (per user request)
        # cursor.execute("UPDATE classified_tickets SET body = '[CONTENT CLEARED ON RESOLUTION]' WHERE id = ?", (ticket_id,))
        
        conn.commit()
        conn.close()
        logger.info(f"Cleaned up data for resolved ticket {ticket_id}")
        return True
    except Exception as e:
        logger.error(f"Error cleaning up ticket {ticket_id}: {e}")
        return False


@app.route('/validate-solution', methods=['POST'])
def validate_solution():
    """
    Validate a specific solution generated by Problem Solver.
    
    This enables the rejection -> feedback -> self-correction loop.
    
    Request body:
    {
        "ticket_id": "WEB-XYZ",
        "solution": "The generated solution text",
        "subject": "Original ticket subject",
        "body": "Original ticket body",
        "triage": { ... triage result ... },
        "is_valid": true,
        "feedback": "..."
    }
    """
    global solver
    
    if solver is None:
        init_solver()
    
    data = request.json
    ticket_id = data.get('ticket_id')
    is_valid_manual = data.get('is_valid') # Frontend sends this directly on manual resolve
    
    # If the frontend is directly marking this as resolved via the UI button
    if is_valid_manual is True and ticket_id:
        try:
            conn = sqlite3.connect(config.DATABASE_PATH)
            conn.execute("UPDATE classified_tickets SET corrected = 1 WHERE id = ?", (ticket_id,))
            conn.commit()
            
            # Get user_slack_id for notification
            cursor = conn.cursor()
            cursor.execute("SELECT user_slack_id, subject FROM classified_tickets WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            user_slack_id = row[0] if row else None
            subject = row[1] if row else "Unknown Subject"
            
            conn.close()
            
            # Clean up PII if needed
            _cleanup_ticket(ticket_id)
            audit_log('TICKET_RESOLVED', ticket_id, str(current_user.id), "Ticket closed directly via manual resolve button")
            
            # ── Update Jira ─────────────────────────────────────────────────────
            # Since this is a manual resolution from the frontend, we update the ticket in Jira
            try:
                # Find Jira key
                jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
                if jira_key:
                    # Resolve in Jira
                    jira_client.update_issue_resolved(
                        jira_key   = jira_key,
                        solution   = data.get('feedback', 'Resolved by human agent via ECE Dashboard.'),
                        ticket_id  = ticket_id,
                        confidence = 1.0, # Human confidence
                    )
            except Exception as jira_e:
                logger.error(f"Error marking Jira issue as resolved for {ticket_id}: {jira_e}")
            # ── End Jira ────────────────────────────────────────────────────────

            # ── Slack Notification ──────────────────────────────────────────
            try:
                slack.notify_resolved(
                    ticket_id=ticket_id,
                    subject=subject,
                    user_slack_id=user_slack_id,
                    jira_key=jira_key,
                )
            except Exception as slack_err:
                logger.error(f"Slack notification for manual resolution failed: {slack_err}")
            # ── End Slack ───────────────────────────────────────────────────
            
            return jsonify({
                'approved': True,
                'feedback': 'Manual resolution accepted.',
                'status': 'APPROVED'
            })
            
        except Exception as e:
            logger.error(f"Manual solution validation error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    # Old logic for AI validation below -----------------------------------------
    solution = data.get('solution', '')
    subject = data.get('subject', '')
    body = data.get('body', '')
    triage = data.get('triage', {})
    user_id_for_ticket = data.get('user_id', None)
    user_slack_id = data.get('user_slack_id', None)
    user_email = request.json.get('user_email', '')
        
    if not subject or not body:
        return jsonify({'error': 'Please provide solution, subject, and body'}), 400
    
    try:
        # Use Problem Solver's validation method
        is_valid, feedback = solver._validate_solution(
            solution=solution,
            subject=subject,
            body=body,
            triage=triage
        )
        
        response = {
            'approved': is_valid,
            'feedback': feedback,
            'status': 'APPROVED' if is_valid else 'REJECTED'
        }
        
        if is_valid:
            # Mark as corrected/resolved
             try:
                tid = data.get('ticket_id')
                if tid:
                    conn = sqlite3.connect(config.DATABASE_PATH)
                    conn.execute("UPDATE classified_tickets SET corrected = 1 WHERE id = ?", (tid,))
                    conn.commit()
                    conn.close()
                    _cleanup_ticket(tid)
                    audit_log('TICKET_RESOLVED', tid, str(current_user.id), "Ticket validated automatically and marked resolved")
             except Exception as db_e:
                 logger.error(f"Error marking resolved: {db_e}")

        if not is_valid:
            response['recommendation'] = 'Self-correct and retry based on feedback'
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Solution validation error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats')
def get_stats():
    """
    Get real dashboard statistics from the database.
    
    Returns actual ticket counts, resolution rates, and performance metrics.
    """
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Total tickets processed
        cursor.execute('SELECT COUNT(*) FROM classified_tickets')
        total_row = cursor.fetchone()
        total_tickets = total_row[0] if total_row else 0
        
        # Tickets processed today
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(
            'SELECT COUNT(*) FROM classified_tickets WHERE DATE(timestamp) = ?',
            (today,)
        )
        today_row = cursor.fetchone()
        today_count = today_row[0] if today_row else 0
        
        # Escalated tickets (tickets in learning_buffer are corrections/escalations)
        cursor.execute('SELECT COUNT(*) FROM learning_buffer')
        escalated_row = cursor.fetchone()
        escalated_count = escalated_row[0] if escalated_row else 0
        
        # Calculate AI success rate (tickets not escalated / total)
        if total_tickets > 0:
            ai_success_rate = ((total_tickets - escalated_count) / total_tickets) * 100
        else:
            ai_success_rate = 0.0
        
        # Average confidence scores
        cursor.execute('''
            SELECT AVG(conf_type), AVG(conf_priority), AVG(conf_queue) 
            FROM classified_tickets
        ''')
        conf_row = cursor.fetchone()
        avg_confidence = {
            'type': round((conf_row[0] or 0) * 100, 1),
            'priority': round((conf_row[1] or 0) * 100, 1),
            'queue': round((conf_row[2] or 0) * 100, 1)
        }
        
        # Recent tickets this week
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        cursor.execute(
            'SELECT COUNT(*) FROM classified_tickets WHERE DATE(timestamp) >= ?',
            (week_ago,)
        )
        week_row = cursor.fetchone()
        week_count = week_row[0] if week_row else 0
        
        conn.close()
        
        return jsonify({
            'total_tickets': total_tickets,
            'resolved_today': today_count,
            'ai_success_rate': round(ai_success_rate, 1),
            'escalated_count': escalated_count,
            'week_count': week_count,
            'avg_confidence': avg_confidence,
            'avg_response_time': 1.4,  # TODO: Implement actual timing
            'timestamp': datetime.now().isoformat()
        })
        
    except sqlite3.OperationalError as e:
        # Table might not exist yet
        return jsonify({
            'total_tickets': 0,
            'resolved_today': 0,
            'ai_success_rate': 0.0,
            'escalated_count': 0,
            'week_count': 0,
            'avg_confidence': {'type': 0, 'priority': 0, 'queue': 0},
            'avg_response_time': 0,
            'error': str(e)
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUTOMATION SPECIALIST ENDPOINTS
# ============================================================================

@app.route('/ticket/confirm/<ticket_id>', methods=['GET'])
def confirm_resolution(ticket_id):
    response = request.args.get('response', '').lower()
    
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, subject, body, status, user_id FROM classified_tickets WHERE id = ?", (ticket_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return "Ticket not found.", 404
            
        t_id, subject, body, status, user_id = row
        
        if status in ['resolved', 'auto_closed']:
            conn.close()
            return f"<html><body style='font-family:sans-serif; text-align:center; padding-top: 50px;'><h2>Ticket #{ticket_id} is already closed.</h2></body></html>"

        # Get User Email
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        u_row = cursor.fetchone()
        user_email = u_row[0] if u_row else None
        
        if response == 'yes':
            cursor.execute("UPDATE classified_tickets SET status = 'resolved', corrected = 1, resolved_at = ? WHERE id = ?", (datetime.now(), ticket_id))
            conn.commit()
            
            # Send warm closing email
            if user_email and automation_specialist is not None and hasattr(automation_specialist, 'gmail_api') and automation_specialist.gmail_api.available:
                try:
                    from email.mime.text import MIMEText
                    from email.mime.multipart import MIMEMultipart
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = f'Ticket #{ticket_id} Closed'
                    msg['To'] = user_email
                    msg['From'] = 'eceproject2026@gmail.com'
                    html = f"Glad we could help! Ticket #{ticket_id} is now closed."
                    msg.attach(MIMEText(html, 'html'))
                    automation_specialist.gmail_api.send(msg)
                except Exception as e:
                    logger.error(f"Failed to send close email: {e}")
            conn.close()
            return f"<html><body style='font-family:sans-serif; text-align:center; padding-top: 50px;'><h2 style='color:green;'>✅ Thank you! Ticket #{ticket_id} is now closed.</h2></body></html>"
            
        elif response == 'no':
            cursor.execute("UPDATE classified_tickets SET status = 'reopened' WHERE id = ?", (ticket_id,))
            conn.commit()
            
            # Email user
            if user_email and automation_specialist is not None and hasattr(automation_specialist, 'gmail_api') and automation_specialist.gmail_api.available:
                try:
                    from email.mime.text import MIMEText
                    from email.mime.multipart import MIMEMultipart
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = f'Update on Ticket #{ticket_id}'
                    msg['To'] = user_email
                    msg['From'] = 'eceproject2026@gmail.com'
                    html = f"We have reopened Ticket #{ticket_id} and escalated its priority. An expert will review it shortly."
                    msg.attach(MIMEText(html, 'html'))
                    automation_specialist.gmail_api.send(msg)
                except Exception as e:
                    pass
            
            conn.close()
            
            # Re-trigger workflow with increased priority
            if workflow_manager is not None:
                try:
                    enhanced_subject = f"[URGENT - REOPENED] {subject}"
                    workflow_manager.process_ticket(
                        subject=enhanced_subject,
                        body=body,
                        user_email=user_email or 'unknown@example.com',
                        user_id=user_id,
                        ticket_id=ticket_id
                    )
                except Exception as wf_e:
                    logger.error(f"Failed to re-trigger workflow for {ticket_id}: {wf_e}")
                    
            return f"<html><body style='font-family:sans-serif; text-align:center; padding-top: 50px;'><h2 style='color:orange;'>Your ticket #{ticket_id} has been reopened and escalated to an expert.</h2></body></html>"
            
    except Exception as e:
        logger.error(f"Error in confirm_resolution: {e}")
        return "An error occurred.", 500

@app.route('/api/admin/ticket/<ticket_id>/claim', methods=['POST'])
@login_required
def claim_ticket(ticket_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE classified_tickets SET status = 'in_progress', human_agent = ? WHERE id = ?", (current_user.username, ticket_id))
        conn.commit()
        conn.close()
        audit_log('TICKET_CLAIMED', ticket_id, str(current_user.id), f"Ticket claimed by agent {current_user.username}")
        return jsonify({'success': True, 'message': 'Ticket claimed'})
    except Exception as e:
        logger.error(f"Error claiming ticket: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/ticket/<ticket_id>/human-resolve', methods=['POST'])
@login_required
def human_resolve_ticket(ticket_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        data = request.json
        resolution_notes = data.get('resolution_notes', '')
        save_to_kb = data.get('save_to_kb', True)
        
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Get ticket details
        cursor.execute("SELECT subject, body, user_id, pred_type, pred_queue FROM classified_tickets WHERE id = ?", (ticket_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Ticket not found'}), 404
            
        subject, body, user_id, p_type, p_queue = row
        
        # Update ticket
        cursor.execute('''
            UPDATE classified_tickets 
            SET status = 'resolved', corrected = 1, resolution_notes = ?, resolved_at = ?, human_agent = ? 
            WHERE id = ?
        ''', (resolution_notes, datetime.now(), current_user.username, ticket_id))
        
        # Save to KB
        if save_to_kb:
            cursor.execute('''
                INSERT INTO knowledge_base (subject, body, solution, source, tags, queue, created_at)
                VALUES (?, ?, ?, 'human_expert', ?, ?, ?)
            ''', (subject, body, resolution_notes, 'human_expert', p_type, p_queue, datetime.now()))
            
        conn.commit()
        audit_log('TICKET_RESOLVED', ticket_id, str(current_user.id), "Ticket resolved via human resolve")
        
        # Send Email
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        u_row = cursor.fetchone()
        if u_row and automation_specialist is not None and hasattr(automation_specialist, 'gmail_api') and automation_specialist.gmail_api.available:
            user_email = u_row[0]
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Update on Ticket #{ticket_id}'
            msg['To'] = user_email
            msg['From'] = 'eceproject2026@gmail.com'
            
            base_url = request.host_url.rstrip('/')
            confirm_yes = f"{base_url}/ticket/confirm/{ticket_id}?response=yes"
            confirm_no = f"{base_url}/ticket/confirm/{ticket_id}?response=no"
            
            html = f"""Our expert team has resolved your issue. Here's what was done: <br>
            <blockquote>{resolution_notes}</blockquote><br>
            Reply if you need anything else.<br><hr><br>
            <b>Did this resolve your issue?</b><br>
            ✅ Yes, close my ticket: <a href="{confirm_yes}">{confirm_yes}</a><br>
            ❌ No, I still need help: <a href="{confirm_no}">{confirm_no}</a>
            """
            msg.attach(MIMEText(html, 'html'))
            try:
                automation_specialist.gmail_api.send(msg)
            except Exception as e:
                logger.error(f"Failed to send expert resolution email: {e}")
                
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in human resolve: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/escalated-tickets', methods=['GET'])
@login_required
def list_escalated_tickets():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT c.id, c.subject, c.pred_type, c.pred_priority, c.pred_queue, 
                   c.timestamp, c.status, c.human_agent, c.user_id, u.username as raised_by
            FROM classified_tickets c
            LEFT JOIN users u ON c.user_id = u.id
            WHERE c.status IN ('escalated', 'in_progress', 'reopened')
            ORDER BY c.timestamp DESC
        ''')
        rows = cursor.fetchall()
        tickets = [dict(row) for row in rows]
        conn.close()
        
        return jsonify(tickets)
    except Exception as e:
        logger.error(f"Error fetching escalated tickets: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/tickets', methods=['GET'])
@login_required
def admin_tickets_filtered():
    """
    Filtered ticket list for the Admin History view.
    Query params:
      - status        : comma-separated statuses, e.g. "escalated,in_progress"
      - escalated_only: "true" → only escalated/in_progress/reopened
      - raised_by     : username to filter by
      - priority      : pred_priority value
      - type          : pred_type value
      - date_from     : YYYY-MM-DD
      - date_to       : YYYY-MM-DD
      - search        : free-text search on subject
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = '''
            SELECT t.id, t.subject, t.pred_type, t.pred_priority, t.pred_queue,
                   t.timestamp, t.corrected, t.status, t.human_agent,
                   t.resolution_notes, t.resolved_at,
                   u.username as raised_by
            FROM classified_tickets t
            LEFT JOIN users u ON t.user_id = u.id
            WHERE u.company_id = ?
        '''
        params = [current_user.company_id]

        statuses = request.args.get('status', '').strip()
        escalated_only = request.args.get('escalated_only', '').lower() == 'true'

        if escalated_only:
            query += " AND t.status IN ('escalated', 'in_progress', 'reopened')"
        elif statuses:
            status_list = [s.strip() for s in statuses.split(',') if s.strip()]
            if status_list:
                placeholders = ','.join('?' * len(status_list))
                query += f" AND t.status IN ({placeholders})"
                params.extend(status_list)

        raised_by = request.args.get('raised_by', '').strip()
        if raised_by:
            query += " AND u.username = ?"
            params.append(raised_by)

        priority = request.args.get('priority', '').strip()
        if priority:
            query += " AND t.pred_priority = ?"
            params.append(priority)

        ticket_type = request.args.get('type', '').strip()
        if ticket_type:
            query += " AND t.pred_type = ?"
            params.append(ticket_type)

        date_from = request.args.get('date_from', '').strip()
        if date_from:
            query += " AND DATE(t.timestamp) >= ?"
            params.append(date_from)

        date_to = request.args.get('date_to', '').strip()
        if date_to:
            query += " AND DATE(t.timestamp) <= ?"
            params.append(date_to)

        search = request.args.get('search', '').strip()
        if search:
            query += " AND t.subject LIKE ?"
            params.append(f'%{search}%')

        query += " ORDER BY t.timestamp DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        tickets = [dict(row) for row in rows]
        conn.close()

        return jsonify(tickets)
    except Exception as e:
        logger.error(f"Error fetching filtered admin tickets: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/api/ticket/<ticket_id>/complete', methods=['POST'])
@login_required
def complete_ticket(ticket_id):
    """
    Called when user clicks 'This resolved it' on the dashboard.
    Marks ticker as completed and updates Jira sync.
    """
    try:
        if automation_specialist:
            # We don't have the full ticket obj here easily, but the prompt says 
            # "After automation_specialist.mark_ticket_completed() succeeds"
            # Let's mock the expected structure or just call it if it expects a dict
            conn = sqlite3.connect(config.DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM classified_tickets WHERE id = ?", (ticket_id,))
            row = cur.fetchone()
            conn.close()
            
            if row:
                ticket_obj = dict(row)
                automation_specialist.mark_ticket_completed(ticket_obj, current_user.email)
                
        # ── Jira sync: mark Done ─────────────────────────────────────────
        try:
            jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
            if jira_key:
                jira_client.update_issue_resolved(
                    jira_key   = jira_key,
                    solution   = "Resolved by user confirmation via ECE dashboard.",
                    ticket_id  = ticket_id,
                    confidence = 1.0,
                )
                logger.info(f"Jira {jira_key} marked Done — user confirmed resolution")
        except Exception as e:
            logger.error(f"Jira sync on user resolution failed: {e}")
        # ── End Jira sync ────────────────────────────────────────────────

        try:
            from src.jira_integration import get_jira_key
            
            # get subject for slack since we don't have it explicitly scoped here
            conn_sub = sqlite3.connect(config.DATABASE_PATH)
            c = conn_sub.cursor()
            c.execute("SELECT subject, user_slack_id FROM classified_tickets WHERE id=?", (ticket_id,))
            sub_row = c.fetchone()
            conn_sub.close()
            
            sub_text = sub_row[0] if sub_row else "Ticket"
            slack_id = sub_row[1] if sub_row else None
            
            slack.notify_resolved(
                ticket_id     = ticket_id,
                subject       = sub_text, 
                user_slack_id = slack_id,
                jira_key      = get_jira_key(config.DATABASE_PATH, ticket_id),
            )
        except Exception as e:
            logger.error(f"Slack resolved notify failed: {e}")

        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.execute(
            "UPDATE classified_tickets SET status = 'resolved', corrected = 1 WHERE id = ?",
            (ticket_id,)
        )
        conn.commit()
        conn.close()
        
        audit_log('TICKET_RESOLVED', ticket_id, str(current_user.id), "Ticket marked as complete from dashboard")

        return jsonify({'success': True, 'message': 'Ticket marked as resolved'})
        
    except Exception as e:
        logger.error(f"Error completing ticket {ticket_id}: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("Starting ECE Agent Web Application...")
    print("=" * 50 + "\n")
    print(f"  Local URL : http://{config.SERVER['host']}:{config.SERVER['port']}")
    print()

    # Start Slack Socket Mode listener in background thread
    slack_thread = threading.Thread(target=start_socket_mode, daemon=True)
    slack_thread.start()

    # ── Start Flask ──────────────────────────────────────────────────
    init_solver()
    app.run(
        debug=config.SERVER['debug'],
        host=config.SERVER['host'],
        port=config.SERVER['port']
    )