
import sqlite3
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config

def check_schema():
    print(f"Checking database at: {config.DATABASE_PATH}")
    if not os.path.exists(config.DATABASE_PATH):
        print("Database file does not exist!")
        return

    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check classified_tickets schema
    print("\nSchema for 'classified_tickets':")
    try:
        cursor.execute("PRAGMA table_info(classified_tickets)")
        columns = cursor.fetchall()
        for col in columns:
            print(col)
            
        # Check if user_id exists
        column_names = [col[1] for col in columns]
        if 'user_id' in column_names:
            print("\n✅ 'user_id' column exists.")
        else:
            print("\n❌ 'user_id' column MISSING!")
            
    except Exception as e:
        print(f"Error reading schema: {e}")
        
    conn.close()

if __name__ == "__main__":
    check_schema()
