
import sqlite3
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config

def debug_data():
    print(f"Checking database at: {config.DATABASE_PATH}")
    if not os.path.exists(config.DATABASE_PATH):
        print("Database file does not exist!")
        return

    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    print("\n--- Users ---")
    cursor.execute("SELECT id, username, company_id FROM users")
    users = cursor.fetchall()
    for u in users:
        print(u)
        
    print("\n--- Classified Tickets (Last 10) ---")
    cursor.execute("SELECT id, subject, user_id, timestamp FROM classified_tickets ORDER BY timestamp DESC LIMIT 10")
    tickets = cursor.fetchall()
    for t in tickets:
        print(t)
            
    conn.close()

if __name__ == "__main__":
    debug_data()
