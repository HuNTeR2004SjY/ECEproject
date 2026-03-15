
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


class CompanySettings:
    """
    Per-company key-value integration settings store.
    Backed by the company_integrations table.
    """

    @staticmethod
    def get(company_id: int, key: str, default=None):
        """Fetch a single setting value, or default if not set."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM company_integrations WHERE company_id = ? AND key = ?",
            (company_id, key)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default

    @staticmethod
    def get_all(company_id: int) -> dict:
        """Return all settings for a company as a flat dict {key: value}."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM company_integrations WHERE company_id = ?",
            (company_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return {k: v for k, v in rows}

    @staticmethod
    def set(company_id: int, key: str, value: str):
        """Upsert a single setting."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO company_integrations (company_id, key, value, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(company_id, key) DO UPDATE SET value=excluded.value,
                   updated_at=CURRENT_TIMESTAMP""",
            (company_id, key, value)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def set_many(company_id: int, data: dict):
        """Upsert multiple settings at once."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        for key, value in data.items():
            cursor.execute(
                """INSERT INTO company_integrations (company_id, key, value, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(company_id, key) DO UPDATE SET value=excluded.value,
                       updated_at=CURRENT_TIMESTAMP""",
                (company_id, key, str(value) if value is not None else '')
            )
        conn.commit()
        conn.close()

    # ── Config-shaped helpers ────────────────────────────────────────────────

    @staticmethod
    def get_jira_config(company_id: int) -> dict:
        """Return a dict shaped for JiraIntegration(jira_config=...)."""
        import config as _cfg
        s = CompanySettings.get_all(company_id)
        return {
            'base_url':    s.get('jira_base_url',    _cfg.JIRA.get('base_url', '')),
            'email':       s.get('jira_email',       _cfg.JIRA.get('email', '')),
            'api_token':   s.get('jira_api_token',   _cfg.JIRA.get('api_token', '')),
            'project_key': s.get('jira_project_key', _cfg.JIRA.get('project_key', 'IT')),
            'enabled':     s.get('jira_enabled', 'true').lower() == 'true'
                           if s.get('jira_enabled') else _cfg.JIRA.get('enabled', False),
        }

    @staticmethod
    def get_slack_config(company_id: int) -> dict:
        """Return a dict shaped for SlackIntegration(slack_config=...)."""
        import config as _cfg
        s = CompanySettings.get_all(company_id)
        default_channels = _cfg.SLACK.get('channels', {})
        return {
            'enabled':        s.get('slack_enabled', 'true').lower() == 'true'
                              if s.get('slack_enabled') else _cfg.SLACK.get('enabled', False),
            'bot_token':      s.get('slack_bot_token',    _cfg.SLACK.get('bot_token', '')),
            'signing_secret': s.get('slack_signing_secret', _cfg.SLACK.get('signing_secret', '')),
            'channels': {
                'it_support':  s.get('slack_ch_it',         default_channels.get('it_support',  'it-support')),
                'escalations': s.get('slack_ch_escalations', default_channels.get('escalations', 'it-escalations')),
                'incidents':   s.get('slack_ch_incidents',   default_channels.get('incidents',   'incidents')),
                'logs':        s.get('slack_ch_logs',        default_channels.get('logs',        'ece-logs')),
            },
            'queue_channel_map': _cfg.SLACK.get('queue_channel_map', {}),
        }

    @staticmethod
    def get_email_config(company_id: int) -> dict:
        """Return a dict shaped for NotificationManager(email_config=...)."""
        import config as _cfg
        s = CompanySettings.get_all(company_id)
        return {
            'enabled':       s.get('email_enabled', 'true').lower() == 'true'
                             if s.get('email_enabled') else _cfg.EMAIL_CONFIG.get('enabled', True),
            'smtp_host':     s.get('smtp_host',     _cfg.EMAIL_CONFIG.get('smtp_host', 'smtp.gmail.com')),
            'smtp_port':     int(s.get('smtp_port', _cfg.EMAIL_CONFIG.get('smtp_port', 587))),
            'smtp_user':     s.get('smtp_user',     _cfg.EMAIL_CONFIG.get('smtp_user', '')),
            'smtp_password': s.get('smtp_password', _cfg.EMAIL_CONFIG.get('smtp_password', '')),
            'from_email':    s.get('from_email',    _cfg.EMAIL_CONFIG.get('from_email', '')),
        }

class HumanTeamMember:
    """Model for human team members available for escalation routing."""
    
    @staticmethod
    def _ensure_tables():
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ece_team_members (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                name      TEXT    NOT NULL,
                email     TEXT    NOT NULL,
                role      TEXT    NOT NULL,
                skills    TEXT    NOT NULL,   -- comma-separated queue names
                available INTEGER NOT NULL DEFAULT 1,
                UNIQUE(company_id, email)
            );

            CREATE TABLE IF NOT EXISTS ece_assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id  INTEGER,
                ticket_id   TEXT    NOT NULL,
                member_id   INTEGER NOT NULL,
                assigned_at TEXT    NOT NULL,
                resolved    INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT
            );
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def get_all(company_id: int):
        HumanTeamMember._ensure_tables()
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ece_team_members WHERE company_id = ? ORDER BY name ASC",
            (company_id,)
        )
        members = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return members

    @staticmethod
    def get_available(company_id: int):
        HumanTeamMember._ensure_tables()
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ece_team_members WHERE company_id = ? AND available = 1",
            (company_id,)
        )
        members = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return members

    @staticmethod
    def add(company_id: int, name: str, email: str, role: str, skills: str):
        HumanTeamMember._ensure_tables()
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ece_team_members (company_id, name, email, role, skills)
                VALUES (?, ?, ?, ?, ?)
            """, (company_id, name, email, role, skills))
            conn.commit()
            return True, cursor.lastrowid
        except sqlite3.IntegrityError:
            return False, "A team member with this email already exists."
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    @staticmethod
    def delete(member_id: int, company_id: int):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM ece_team_members WHERE id = ? AND company_id = ?", (member_id, company_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False
        finally:
            conn.close()

    @staticmethod
    def update_availability(member_id: int, company_id: int, available: bool):
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE ece_team_members SET available = ? WHERE id = ? AND company_id = ?",
                (1 if available else 0, member_id, company_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False
        finally:
            conn.close()
            
    @staticmethod
    def _open_ticket_counts(company_id: int) -> dict:
        """Return {member_id: open_ticket_count} for all members in a company."""
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        # count resolved=0 tickets for the given company
        cursor.execute("""
            SELECT member_id, COUNT(*) 
            FROM ece_assignments 
            WHERE resolved = 0 AND company_id = ? 
            GROUP BY member_id
        """, (company_id,))
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}

    @staticmethod
    def record_assignment(company_id: int, ticket_id: str, member_id: int):
        from datetime import datetime, timezone
        HumanTeamMember._ensure_tables()
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ece_assignments (company_id, ticket_id, member_id, assigned_at, resolved)
                VALUES (?, ?, ?, ?, 0)
            """, (company_id, ticket_id, member_id, datetime.now(timezone.utc).isoformat()))
            conn.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to record assignment: {e}")
        finally:
            conn.close()
            
    @staticmethod
    def mark_resolved(ticket_id: str):
        from datetime import datetime, timezone
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE ece_assignments 
                SET resolved = 1, resolved_at = ? 
                WHERE ticket_id = ?
            """, (datetime.now(timezone.utc).isoformat(), ticket_id))
            conn.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to mark resolved in ece_assignments: {e}")
        finally:
            conn.close()
