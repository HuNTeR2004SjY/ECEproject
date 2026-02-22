
import sys
import os
import sqlite3
import json
from flask import Flask

# Add project root to path
sys.path.append(os.getcwd())

from src.models import User
import config

app = Flask(__name__)

def test_data():
    try:
        # Create a dummy user if not exists to ensure we can query
        # But User.get_user_tickets needs a user_id.
        # Let's get an existing user or create one.
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users LIMIT 1")
        user = cursor.fetchone()
        
        if not user:
            print("No users found. Creating one.")
            User.create_user(1, 'testuser', 'password', 'test@example.com')
            cursor.execute("SELECT id FROM users WHERE username='testuser'")
            user = cursor.fetchone()
            
        user_id = user[0]
        print(f"Testing with user_id: {user_id}")
        
        tickets = User.get_user_tickets(user_id)
        print(f"Fetched {len(tickets)} tickets")
        
        # Test JSON serialization
        with app.app_context():
            from flask import json
            json_output = json.dumps(tickets)
            print("JSON serialization successful")
            print(json_output[:100] + "...")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_data()
