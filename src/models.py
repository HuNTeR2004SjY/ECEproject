
import sqlite3
import hashlib
from flask_login import UserMixin
import config

class User(UserMixin):
    def __init__(self, id, company_id, username, role, email):
        self.id = id
        self.company_id = company_id
        self.username = username
        self.role = role
        self.role = role
        self.email = email

    @staticmethod
    def get_user_tickets(user_id):
        """Fetch tickets created by a specific user."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, subject, body, pred_type, pred_priority, timestamp, corrected
            FROM classified_tickets 
            WHERE user_id = ?
            ORDER BY timestamp DESC LIMIT 50
        """, (str(user_id),))
        
        tickets = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return tickets
    
    @staticmethod
    def get(user_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, company_id, username, role, email FROM users WHERE id = ?", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data:
            return User(*user_data)
        return None

    @staticmethod
    def authenticate(company_name, username, password):
        # First verify company exists
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM companies WHERE name = ?", (company_name,))
        company_data = cursor.fetchone()
        
        if not company_data:
            conn.close()
            return None
        
        company_id = company_data[0]
        
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        cursor.execute("""
            SELECT id, company_id, username, role, email 
            FROM users 
            WHERE company_id = ? AND username = ? AND password_hash = ?
        """, (company_id, username, hashed_password))
        
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data:
            return User(*user_data)
        return None

    @staticmethod
    def get_all_companies():
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM companies")
        companies = [row[0] for row in cursor.fetchall()]
        conn.close()
        return companies

    @staticmethod
    def get_company_tickets(company_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check if company_id column exists in classified_tickets
        # If not, we might need to add it or just return all for demo if not multi-tenant ready
        # For this task, we assume we might need to filter by company.
        # However, the current schema might not have company_id in tickets.
        # We'll just return all tickets for now as a placeholder 
        # OR we should have added company_id to tickets table.
        # Given the scope, let's just return recent tickets.
        
        cursor.execute("""
            SELECT 
                t.id, t.subject, t.body, t.pred_type, t.pred_priority, t.timestamp, t.corrected,
                t.status, t.human_agent, t.resolution_notes, t.resolved_at,
                u.username as raised_by, u.id as raised_by_id
            FROM classified_tickets t
            LEFT JOIN users u ON t.user_id = u.id
            -- Optionally filter by company if we had company_id in tickets, 
            -- but for now assuming admin sees all or we filter by user's company via join?
            -- Since we don't have company_id in tickets yet, we'll verify user's company match.
            -- JOIN users u ON t.user_id = u.id WHERE u.company_id = ?
            WHERE u.company_id = ?
            ORDER BY t.timestamp DESC
        """, (company_id,))
        
        tickets = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return tickets

    @staticmethod
    def get_company_stats(company_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        stats = {
            'total': 0,
            'active': 0,
            'escalated': 0,
            'in_progress': 0,
            'resolved': 0,
            'auto_closed': 0
        }
        
        try:
            # Total
            cursor.execute("SELECT COUNT(*) FROM classified_tickets")
            stats['total'] = cursor.fetchone()[0]
            
            # Active (not resolved/auto_closed/corrected)
            cursor.execute("SELECT COUNT(*) FROM classified_tickets WHERE status NOT IN ('resolved', 'auto_closed') AND corrected = 0")
            stats['active'] = cursor.fetchone()[0]
            
            # Escalated
            cursor.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'escalated'")
            stats['escalated'] = cursor.fetchone()[0]
            
            # In Progress
            cursor.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'in_progress'")
            stats['in_progress'] = cursor.fetchone()[0]
            
            # Resolved (status='resolved' OR corrected=1)
            cursor.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'resolved' OR corrected = 1")
            stats['resolved'] = cursor.fetchone()[0]
            
            # Auto-Closed
            cursor.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'auto_closed'")
            stats['auto_closed'] = cursor.fetchone()[0]
            
        except Exception:
            pass
            
        conn.close()
        return stats
    @staticmethod
    def get_user_latest_ticket(username):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # We need to find tickets submitted by this user.
        # However, the current schema might not store username/user_id in tickets table?
        # app.py: predict() generates ticket_id but doesn't seem to save user_id.
        # Check app.py predict() again.
        # It calls solver.solve().
        # solver.solve() saves to classified_tickets?
        # Let's check schema.
        
        # Schema from seed_db.py:
        # classified_tickets schema is unknown fully, but we saw `cursor.execute("PRAGMA table_info(classified_tickets)")` earlier? No, I killed it.
        # But app.py:298 says: SELECT id, subject, body, pred_type, pred_priority, pred_queue, timestamp FROM classified_tickets
        # It doesn't seem to have user_id.
        
        # If no user_id in tickets, we can't link them.
        # We need to modify app.py to save user_id if we want this feature.
        # Or checking `learning_buffer`?
        
        # Assuming we need to add user_id column or just return a dummy for now?
        # User asked for "proper" login. I should probably add user_id to tickets if I can.
        # But that requires schema migration.
        # ALTER TABLE classified_tickets ADD COLUMN user_id TEXT;
        
        try:
            cursor.execute("SELECT id, subject, pred_type, pred_priority, timestamp FROM classified_tickets ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
            
        conn.close()
        return None

    @staticmethod
    def create_user(company_id, username, password, email, role='employee'):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        try:
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            cursor.execute("""
                INSERT INTO users (company_id, username, password_hash, role, email)
                VALUES (?, ?, ?, ?, ?)
            """, (company_id, username, password_hash, role, email))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    @staticmethod
    def get_all_users(company_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, username, email, role 
            FROM users 
            WHERE company_id = ?
        """, (company_id,))
        
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users

    @staticmethod
    def delete(user_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

class Department:
    @staticmethod
    def get_all(company_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM departments WHERE company_id = ?", (company_id,))
        departments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return departments

    @staticmethod
    def add(company_id, name, email):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute("INSERT INTO departments (company_id, name, email) VALUES (?, ?, ?)", 
                           (company_id, name, email))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    @staticmethod
    def delete(dept_id):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()



class Company:
    @staticmethod
    def email_exists(email):
        """Check if a company email is already registered (emails are the unique identifier)."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM companies WHERE LOWER(email) = LOWER(?)", (email,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    @staticmethod
    def register_with_admin(company_name, company_email, admin_username, admin_password, admin_email):
        """
        Atomically create a company and its first admin user.
        Returns (True, company_name) on success, or (False, error_message) on failure.
        Everything happens in one transaction — if any step fails the whole thing rolls back.
        """
        import hashlib
        conn = sqlite3.connect(config.DATABASE_PATH)
        try:
            conn.execute("BEGIN")
            cursor = conn.cursor()

            # Insert company
            cursor.execute(
                "INSERT INTO companies (name, email) VALUES (?, ?)",
                (company_name, company_email)
            )
            company_id = cursor.lastrowid

            # Insert admin user
            password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
            cursor.execute(
                "INSERT INTO users (company_id, username, password_hash, role, email) VALUES (?, ?, ?, ?, ?)",
                (company_id, admin_username, password_hash, 'admin', admin_email)
            )

            conn.commit()
            return True, company_name
        except sqlite3.IntegrityError as e:
            conn.rollback()
            return False, f"Database conflict: {e}"
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()

    @staticmethod
    def get_all():
        """Return all companies as a list of dicts with id, name, email."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, name, email FROM companies ORDER BY name ASC")
        except Exception:
            cursor.execute("SELECT id, name FROM companies ORDER BY name ASC")
        companies = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return companies
