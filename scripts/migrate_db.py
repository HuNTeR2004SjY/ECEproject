import sqlite3
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import config

def migrate():
    print("Migrating database...")
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if user_id column exists
        cursor.execute("PRAGMA table_info(classified_tickets)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'user_id' not in columns:
            print("Adding user_id column to classified_tickets...")
            cursor.execute("ALTER TABLE classified_tickets ADD COLUMN user_id TEXT")
            print("Column added.")
        else:
            print("user_id column already exists.")
            
    except Exception as e:
        print(f"Migration error: {e}")
        
    finally:
        conn.commit()
        conn.close()
        print("Migration complete.")

if __name__ == "__main__":
    migrate()
