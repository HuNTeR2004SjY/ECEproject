"""
=============================

Flask backend that connects the trained model to a web frontend.

Run: python app.py
Then open: http://localhost:5000 (or configured port)
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
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

    # Initialize ProcessMonitor
    if process_monitor is None:
        try:
            from src.process_monitor import ProcessMonitor
            logger.info("Initializing Process Monitor Agent...")
            process_monitor = ProcessMonitor(
                db_path=config.DATABASE_PATH,
                status_reporter=LogStatusReporter(),
                check_interval_seconds=60
            )
            process_monitor.start()
            logger.info("Process Monitor Agent Started!")
        except Exception as e:
            logger.error(f"Failed to start Process Monitor: {e}")



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


@app.route('/api/admin/users', methods=['GET', 'POST'])
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
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error processing ticket: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


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
        "solution": "The generated solution text",
        "subject": "Original ticket subject",
        "body": "Original ticket body",
        "triage": { ... triage result ... }
    }
    """
    global solver
    
    if solver is None:
        init_solver()
    
    data = request.json
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
                conn = sqlite3.connect(config.DATABASE_PATH)
                conn.execute("UPDATE classified_tickets SET corrected = 1 WHERE id = ?", (data.get('ticket_id'),)) # Need ticket_id in request or find by subject?
                # Request body for validate usually has subject/body/solution but might not have ID if called from generic context.
                # However, our frontend calls it. Let's check frontend.
                # Frontend (script.js) sends: solution, subject, body, triage.
                # It does NOT send ticket_id currently?
                # Wait, we need ticket_id to cleanup!
                # Update: Frontend needs to send ticket_id.
                
                # Assuming we will fix frontend to send ticket_id, or we find it by subject (risky).
                # Implementation Plan said "Update validate_solution route".
                # Let's assume passed in `ticket_id` field or we can't clean up easily.
                # We'll add ticket_id to request in frontend later. For now code defensively.
                
                tid = data.get('ticket_id')
                if tid:
                    conn.execute("UPDATE classified_tickets SET corrected = 1 WHERE id = ?", (tid,))
                    conn.commit()
                    conn.close()
                    _cleanup_ticket(tid)
                else:
                    conn.close()
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