
import sqlite3
import config
from datetime import datetime

def create_table():
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create classified_tickets table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS classified_tickets (
        id TEXT PRIMARY KEY,
        subject TEXT,
        body TEXT,
        pred_type TEXT,
        pred_priority TEXT,
        pred_queue TEXT,
        timestamp DATETIME,
        corrected BOOLEAN DEFAULT 0
    )
    ''')
    
    # Create learning_buffer table (stats uses it)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS learning_buffer (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT,
        body TEXT,
        answer TEXT,
        type TEXT,
        priority TEXT,
        queue TEXT
    )
    ''')

    # Seed a sample ticket
    try:
        cursor.execute("SELECT COUNT(*) FROM classified_tickets")
        if cursor.fetchone()[0] == 0:
            print("Seeding sample ticket...")
            cursor.execute("""
                INSERT INTO classified_tickets (id, subject, body, pred_type, pred_priority, pred_queue, timestamp, corrected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ('SAMPLE-001', 'Login failure', 'Cannot login to the system.', 'Incident', 'High', 'L1 Support', datetime.now(), 0))
    except Exception as e:
        print(f"Error seeding: {e}")

    conn.commit()
    conn.close()
    print("Tables created/verified.")

if __name__ == "__main__":
    create_table()
