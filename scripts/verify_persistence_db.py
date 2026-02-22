
import sqlite3
import requests
import time
import sys
import threading
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:5000"
DB_PATH = r"e:\College\main pro\VishnuSide\ECE\tickets.db"

def check_server():
    """Check if server is running."""
    try:
        response = requests.get(BASE_URL)
        return response.status_code == 200 or response.status_code == 302
    except requests.ConnectionError:
        return False

def verify_tickets_persisted():
    print("\nVerifying Ticket Persistence in Database...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check for recent tickets with user_id
    cursor.execute("""
        SELECT id, subject, user_id, timestamp 
        FROM classified_tickets 
        ORDER BY timestamp DESC LIMIT 5
    """)
    
    rows = cursor.fetchall()
    
    if not rows:
        print("X No tickets found in database.")
        conn.close()
        return False
    
    print(f"Found {len(rows)} recent tickets.")
    for row in rows:
        print(f"   - ID: {row[0]}, Subject: {row[1]}, UserID: {row[2]}, Time: {row[3]}")
        
        # Verify ID format (simple check)
        if len(row[0]) < 8:
             print(f"   Warning: Ticket ID {row[0]} seems short.")
        
        # Verify user_id is present (might be 'unknown' or actual ID depending on login state)
        if row[2] is None:
             print(f"   Error: user_id is NULL for ticket {row[0]}.")
        else:
             print(f"   user_id present: {row[2]}")
             
    conn.close()
    return True

if __name__ == "__main__":
    if check_server():
        print("Server is running. Best to test manually via UI to include login session.")
        print("   This script only checks the DB state.")
    else:
        print("Server not running.")
        
    verify_tickets_persisted()
