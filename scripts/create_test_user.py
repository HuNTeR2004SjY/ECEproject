
import sqlite3
import hashlib
import os
import sys

# Ensure we can find config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_test_user():
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Get Company ID
    cursor.execute("SELECT id FROM companies WHERE name = 'TechCorp'")
    company = cursor.fetchone()
    if not company:
        print("TechCorp not found!")
        return
    
    company_id = company[0]
    username = 'testuser'
    password = 'testpass'
    hashed = hash_password(password)
    
    try:
        cursor.execute("""
            INSERT INTO users (company_id, username, password_hash, role, email)
            VALUES (?, ?, ?, 'employee', 'testuser@techcorp.com')
        """, (company_id, username, hashed))
        conn.commit()
        print(f"User {username} created successfully.")
    except sqlite3.IntegrityError:
        print(f"User {username} already exists. Updating password.")
        cursor.execute("""
            UPDATE users SET password_hash = ? WHERE username = ?
        """, (hashed, username))
        conn.commit()
        print(f"User {username} password updated.")
        
    conn.close()

if __name__ == "__main__":
    create_test_user()
