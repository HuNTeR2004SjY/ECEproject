import sqlite3
import os

DB_PATH = 'data/tickets.db'

def update_schema():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check current columns
        cursor.execute("PRAGMA table_info(classified_tickets)")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"Current columns: {columns}")
        
        if 'status' not in columns:
            print("Adding 'status' column...")
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN status TEXT DEFAULT 'solution_proposed'")
            print("Column added successfully.")
        else:
            print("'status' column already exists.")
            
        conn.commit()
    except Exception as e:
        print(f"Error updating schema: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    update_schema()
