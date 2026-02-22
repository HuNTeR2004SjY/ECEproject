
import sqlite3
import hashlib
import os

DB_PATH = 'tickets.db'

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create Companies Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        domain TEXT
    )
    ''')

    # Create Users Table
    # role: 'admin' or 'employee'
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'employee',
        email TEXT,
        FOREIGN KEY (company_id) REFERENCES companies (id)
    )
    ''')
    
    # Check if data exists
    cursor.execute("SELECT COUNT(*) FROM companies")
    if cursor.fetchone()[0] == 0:
        print("Seeding initial data...")
        
        # Add Companies
        cursor.execute("INSERT INTO companies (name, domain) VALUES (?, ?)", ('TechCorp', 'techcorp.com'))
        tech_corp_id = cursor.lastrowid
        
        cursor.execute("INSERT INTO companies (name, domain) VALUES (?, ?)", ('GlobalSolutions', 'globalsolutions.com'))
        global_solutions_id = cursor.lastrowid
        
        # Add Users for TechCorp
        # Admin
        cursor.execute("INSERT INTO users (company_id, username, password_hash, role, email) VALUES (?, ?, ?, ?, ?)",
                       (tech_corp_id, 'admin', hash_password('admin123'), 'admin', 'admin@techcorp.com'))
        
        # Employee
        cursor.execute("INSERT INTO users (company_id, username, password_hash, role, email) VALUES (?, ?, ?, ?, ?)",
                       (tech_corp_id, 'emp001', hash_password('user123'), 'employee', 'employee@techcorp.com'))

        print(f"Created Admin: admin / admin123 (Company: TechCorp)")
        print(f"Created Employee: emp001 / user123 (Company: TechCorp)")
    else:
        print("Database already seeded.")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
