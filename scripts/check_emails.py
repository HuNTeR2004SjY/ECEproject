
import sqlite3
import os
import sys

# Ensure we can find config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def check_emails():
    print(f"Checking database at: {config.DATABASE_PATH}")
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        print("\n=== USERS ===")
        cursor.execute("SELECT id, username, role, email FROM users")
        users = cursor.fetchall()
        for u in users:
            print(f"User: {u}")
            
        print("\n=== DEPARTMENTS ===")
        cursor.execute("SELECT id, name, email FROM departments")
        depts = cursor.fetchall()
        for d in depts:
            print(f"Dept: {d}")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_emails()
