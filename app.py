"""
=============================

Flask backend that connects the trained model to a web frontend.

Run: python app.py
Then open: http://localhost:5000 (or configured port)
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, Response, stream_with_context
import sys
import uuid
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
from src.models import User, Department
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
process_monitor = None
xai_wrapper = ExplainableTriageWrapper()
pattern_miner = PatternMiner(db_path=config.DATABASE_PATH)
workflow_manager = None
jira_client = JiraIntegration()


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
        
        # Add columns if not exist
        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN human_agent TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN resolution_notes TEXT")
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
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

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
            logger.error(f"Jira integration block failed: {jira_err}")
        # ── End Jira ─────────────────────────────────────────────────────

        response['jira_key'] = jira_key

        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error processing ticket: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


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

            # --- STEP 2: Full solve (slow, uses triage result internally) ---
            logger.info(f"[STREAM] Solver starting for ticket {ticket_id}")
            result = solver.solve(subject=subject, body=body, ticket_id=ticket_id)

            # --- Save ticket to DB ---
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                cursor = conn.cursor()
                status = 'escalated' if result.get('escalated') else 'solution_proposed'

                cursor.execute('''
                    INSERT INTO classified_tickets
                    (id, subject, body, pred_type, pred_priority, pred_queue, timestamp, corrected, user_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ''', (
                    ticket_id, subject, body,
                    result['triage']['type'], result['triage']['priority'], result['triage']['queue'],
                    datetime.now(), user_id_str if user_authenticated else None, status
                ))

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
                logger.info(f"[STREAM] Ticket {ticket_id} saved (status: {status})")

                # Notifications
                try:
                    if automation_specialist is None:
                        init_solver()
                    ticket_data = {
                        'id': ticket_id, 'subject': subject, 'body': body,
                        'type': result['triage']['type'], 'priority': result['triage']['priority'],
                        'user_id': user_id_str, 'status': status
                    }
                    automation_specialist.notify_ticket_resolution(
                        ticket_data=ticket_data, result=result, user_email=user_email
                    )
                except Exception as notify_err:
                    logger.error(f"[STREAM] Notification failed: {notify_err}")

            except Exception as db_err:
                logger.error(f"[STREAM] DB save failed: {db_err}")

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

@app.route('/api/ticket/<ticket_id>/explanation', methods=['GET'])
@login_required
def get_ticket_explanation(ticket_id):
    """
    Regenerates the Explainable AI rationale for a historical ticket.
    """
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # 1. Fetch ticket subject, body, and predictions
        cursor.execute('''
            SELECT subject, body, pred_type, pred_priority, pred_queue 
            FROM classified_tickets 
            WHERE id = ?
        ''', (ticket_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return jsonify({'error': 'Ticket not found'}), 404
            
        subject, body, p_type, p_priority, p_queue = row
        
        # 2. Fetch tags (stored in DB as a comma-separated string or in ticket_tags if changed)
        # Note: If tags aren't logged in DB, we mock empty.
        tags = []
        
        # Reconstruct the triage_result dict using default confidences (.75) per requirements
        triage_result = {
            'type': p_type,
            'type_confidence': 0.75,
            'priority': p_priority,
            'priority_confidence': 0.75,
            'queue': p_queue,
            'queue_confidence': 0.75,
            'tags': tags
        }
        
        # Generate Explanation
        explanation = xai_wrapper.explain(triage_result, subject, body)
        conn.close()
        
        return jsonify({'explanation': explanation.to_dict()})
        
    except Exception as e:
        logger.error(f"Error fetching XAI explanation for {ticket_id}: {e}")
        return jsonify({'error': 'Failed to generate explanation'}), 500

@app.route('/api/admin/systemic-alerts', methods=['GET'])
@login_required
def get_systemic_alerts():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        alerts = pattern_miner.get_active_alerts(50)
        return jsonify({'alerts': alerts, 'count': len(alerts)})
    except Exception as e:
        logger.error(f"Error fetching systemic alerts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/ticket/<ticket_id>/cluster', methods=['GET'])
@login_required
def get_ticket_cluster(ticket_id):
    try:
        cluster = pattern_miner.get_cluster_for_ticket(ticket_id)
        if cluster:
            return jsonify({'in_cluster': True, 'cluster': cluster})
        return jsonify({'in_cluster': False})
    except Exception as e:
        logger.error(f"Error fetching cluster for {ticket_id}: {e}")
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
            conn.close()
            
            # Clean up PII if needed
            _cleanup_ticket(ticket_id)
            
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
    
    if not solution or not subject or not body:
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


@app.route('/api/escalated')
def get_escalated_tickets():
    """
    Get list of escalated tickets for human review.
    """
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Get tickets that need human review (corrected = 0 means not yet resolved)
        cursor.execute('''
            SELECT id, subject, body, pred_type, pred_priority, pred_queue, timestamp
            FROM classified_tickets 
            WHERE corrected = 0 AND conf_queue < ?
            ORDER BY timestamp DESC
            LIMIT 50
        ''', (config.VALIDATION['low_confidence_queue_threshold'],))
        
        tickets = []
        for row in cursor.fetchall():
            tickets.append({
                'id': row[0],
                'subject': row[1],
                'body': row[2][:200] + '...' if len(row[2]) > 200 else row[2],
                'type': row[3],
                'priority': row[4],
                'queue': row[5],
                'timestamp': row[6]
            })
        
        conn.close()
        return jsonify({'tickets': tickets, 'count': len(tickets)})
        
    except Exception as e:
        logger.error(f"Error fetching escalated tickets: {e}")
        return jsonify({'error': str(e), 'tickets': []}), 500


@app.route('/api/escalated/<int:ticket_id>/resolve', methods=['POST'])
def resolve_escalated_ticket(ticket_id):
    """
    Submit human resolution for an escalated ticket.
    This saves to the learning buffer for future model retraining.
    """
    data = request.json
    solution = data.get('solution', '')
    correct_type = data.get('correct_type')
    correct_priority = data.get('correct_priority')
    correct_queue = data.get('correct_queue')
    
    if not solution:
        return jsonify({'error': 'Solution is required'}), 400
    
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Get original ticket
        cursor.execute(
            'SELECT subject, body, pred_type, pred_priority, pred_queue FROM classified_tickets WHERE id = ?',
            (ticket_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return jsonify({'error': 'Ticket not found'}), 404
        
        subject, body, pred_type, pred_priority, pred_queue = row
        
        # Use corrected values or predictions
        final_type = correct_type or pred_type
        final_priority = correct_priority or pred_priority
        final_queue = correct_queue or pred_queue
        
        # Add to learning buffer
        cursor.execute('''
            INSERT INTO learning_buffer (subject, body, answer, type, priority, queue)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (subject, body, solution, final_type, final_priority, final_queue))
        
        # Mark as corrected
        # Mark as corrected
        cursor.execute(
            'UPDATE classified_tickets SET corrected = 1 WHERE id = ?',
            (ticket_id,)
        )
        
        conn.commit()
        conn.close()
        
        # Clean up data
        _cleanup_ticket(ticket_id)
        
        return jsonify({
            'success': True,
            'message': 'Ticket resolved and added to learning buffer'
        })
        
    except Exception as e:
        logger.error(f"Error resolving ticket: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/quality-report')
def get_quality_report():
    """Get the latest quality validation report."""
    try:
        import json
        report_path = 'quality_report.json'
        with open(report_path, 'r') as f:
            report = json.load(f)
        return jsonify(report)
    except FileNotFoundError:
        return jsonify({'error': 'No validation report found. Run /validate first.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUTOMATION SPECIALIST ENDPOINTS
# ============================================================================

@app.route('/api/ticket/<ticket_id>/status', methods=['GET'])
def get_ticket_status(ticket_id):
    """
    Get current status of a ticket
    
    Returns ticket status, progress, and estimated completion
    """
    try:
        # In real implementation, fetch from database
        # For now, return mock data
        return jsonify({
            'ticket_id': ticket_id,
            'status': 'processing',
            'last_updated': datetime.now().isoformat(),
            'progress': 60,
            'message': 'AI is working on your issue'
        })
    except Exception as e:
        logger.error(f"Error fetching ticket status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ticket/<ticket_id>/execute', methods=['POST'])
def execute_ticket_solution(ticket_id):
    """
    Execute or re-execute solution for a ticket
    
    This endpoint is called when:
    - Quality Gatekeeper approves a solution (automatic)
    - User manually triggers execution (manual)
    """
    global automation_specialist
    
    if automation_specialist is None:
        init_solver()
    
    try:
        data = request.json or {}
        user_email = data.get('user_email', 'eceproject2026+user@gmail.com')
        
        # Create mock ticket for demonstration
        ticket = {
            'id': ticket_id,
            'user_id': data.get('user_id', 'demo_user'),
            'subject': data.get('subject', 'Demo Ticket'),
            'body': data.get('body', 'Demo ticket body'),
            'type': data.get('type', 'incident'),
            'priority': data.get('priority', 'medium'),
            'status': 'approved'
        }
        
        # Mock approved solution
        approved_solution = {
            'text': data.get('solution', 'Please follow these steps to resolve...'),
            'confidence': data.get('confidence', 0.85)
        }
        
        # Process solution through Automation Specialist
        result = automation_specialist.process_approved_solution(
            ticket=ticket,
            approved_solution=approved_solution,
            user_email=user_email
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error executing solution: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/ticket/<ticket_id>/escalate', methods=['POST'])
def escalate_ticket_manual(ticket_id):
    """
    Manually escalate a ticket to human team
    
    Called when user requests escalation or automatic escalation is needed
    """
    global automation_specialist
    
    if automation_specialist is None:
        init_solver()
    
    try:
        data = request.json or {}
        user_email = data.get('user_email', 'eceproject2026+user@gmail.com')
        reason = data.get('reason', 'User requested escalation')
        
        # Create mock ticket
        ticket = {
            'id': ticket_id,
            'user_id': data.get('user_id', 'demo_user'),
            'subject': data.get('subject', 'Demo Ticket'),
            'body': data.get('body', 'Demo ticket body'),
            'type': data.get('type', 'incident'),
            'priority': data.get('priority', 'high'),
            'status': 'processing'
        }
        
        # Escalate ticket
        escalation = automation_specialist.escalation_manager.escalate_ticket(
            ticket=ticket,
            reason=reason,
            user_email=user_email
        )
        
        return jsonify({
            'success': True,
            'escalation': escalation,
            'message': f'Ticket escalated to {escalation["department"]} department'
        })
        
    except Exception as e:
        logger.error(f"Error escalating ticket: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/jira-status', methods=['GET'])
@login_required
def get_jira_status():
    """
    Look up the last 20 tickets and their Jira status.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
        
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get last 20 tickets
        cursor.execute("SELECT id FROM classified_tickets ORDER BY timestamp DESC LIMIT 20")
        recent_tickets = cursor.fetchall()
        
        linked = []
        unlinked_count = 0
        
        for rt in recent_tickets:
            tid = rt['id']
            # Lookup in jira_keys
            cursor.execute("SELECT jira_key, created_at FROM jira_keys WHERE ticket_id = ?", (tid,))
            j_row = cursor.fetchone()
            if j_row:
                linked.append({
                    "ticket_id": tid,
                    "jira_key": j_row['jira_key'],
                    "created_at": j_row['created_at']
                })
            else:
                unlinked_count += 1
                
        conn.close()
        
        return jsonify({
            "linked": linked,
            "unlinked_count": unlinked_count,
            "jira_enabled": jira.enabled
        })
        
    except Exception as e:
        logger.error(f"Error fetching Jira status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/user/notifications', methods=['GET'])
def get_user_notifications():
    """
    Get all notifications for the logged-in user
    
    Called when:
    - User logs in (to show pop-ups)
    - User opens notifications panel
    """
    global automation_specialist
    
    if automation_specialist is None:
        init_solver()
    
    try:
        user_id = request.args.get('user_id', 'demo_user')
        limit = int(request.args.get('limit', 50))
        
        # Get notification history
        notifications = automation_specialist.notification_manager.notification_history
        
        # Filter by user and limit
        user_notifications = [
            n for n in notifications
            if n.get('user_id') == user_id
        ][:limit]
        
        unread_count = sum(1 for n in user_notifications if not n.get('read', False))
        
        return jsonify({
            'notifications': user_notifications,
            'unread_count': unread_count,
            'total_count': len(user_notifications)
        })
        
    except Exception as e:
        logger.error(f"Error fetching notifications: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/user/latest-ticket')
@login_required
def get_user_latest_ticket():
    """Get the specific user's latest ticket status."""
    ticket = User.get_user_latest_ticket(current_user.username)
    if ticket:
        return jsonify(ticket)
    return jsonify(None)

@app.route('/api/ticket/<ticket_id>/complete', methods=['POST'])
def mark_ticket_complete(ticket_id):
    """
    Mark ticket as completed
    
    Called when user confirms the issue is resolved
    """
    global automation_specialist
    
    if automation_specialist is None:
        init_solver()
    
    try:
        data = request.json or {}
        user_email = data.get('user_email', 'user@example.com')
        feedback = data.get('feedback')
        
        # Create mock ticket
        ticket = {
            'id': ticket_id,
            'user_id': data.get('user_id', 'demo_user'),
            'subject': data.get('subject', 'Demo Ticket'),
            'status': 'awaiting_user'
        }
        
        # Mark as completed
        updated_ticket = automation_specialist.mark_ticket_completed(
            ticket=ticket,
            user_email=user_email,
            user_feedback=feedback
        )
        
        return jsonify({
            'success': True,
            'ticket': updated_ticket,
            'message': 'Ticket marked as completed'
        })
        
    except Exception as e:
        logger.error(f"Error completing ticket: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/notifications/send-test', methods=['POST'])
def send_test_notification():
    """
    Send a test notification (for testing/demo purposes)
    """
    global automation_specialist
    
    if automation_specialist is None:
        init_solver()
    
    try:
        data = request.json or {}
        user_email = data.get('email', 'test@example.com')
        
        # Create test ticket
        test_ticket = {
            'id': 'TEST-001',
            'user_id': 'test_user',
            'subject': 'Test Notification',
            'body': 'This is a test notification',
            'type': 'incident',
            'priority': 'normal',
            'status': 'solution_ready'
        }
        
        # Send notification
        from automation_specialist import NotificationChannel
        result = automation_specialist.notification_manager.send_notification(
            ticket=test_ticket,
            event_type='solution_proposed',
            channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
            user_email=user_email,
            user_id='test_user'
        )
        
        return jsonify({
            'success': True,
            'result': result,
            'message': f'Test notification sent to {user_email}'
        })
        
    except Exception as e:
        logger.error(f"Error sending test notification: {e}")
        return jsonify({'error': str(e)}), 500


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

if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("Starting ECE Agent Web Application...")
    print(f"Open http://{config.SERVER['host']}:{config.SERVER['port']} in your browser")
    print("=" * 50 + "\n")
    
    # Initialize model before starting server
    init_solver()

    app.run(
        debug=config.SERVER['debug'], 
        host=config.SERVER['host'], 
        port=config.SERVER['port']
    )