import sqlite3
import config
import sys

def main():
    try:
        conn = sqlite3.connect(config.DATABASE_PATH, timeout=20)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM users WHERE username = 'emp001'")
        user = cursor.fetchone()
        
        if not user:
            print("User emp001 not found.")
            return
            
        user_id = user[0]
        
        # Delete interactions first
        cursor.execute('''
            DELETE FROM ticket_interactions 
            WHERE ticket_id IN (
                SELECT id FROM classified_tickets WHERE user_id = ?
            )
        ''', (user_id,))
        interactions_deleted = cursor.rowcount
        
        # Delete tickets
        cursor.execute('DELETE FROM classified_tickets WHERE user_id = ?', (user_id,))
        tickets_deleted = cursor.rowcount
        
        conn.commit()
        print(f"SUCCESS: Deleted {tickets_deleted} tickets and {interactions_deleted} interactions for emp001.")
        
    except sqlite3.OperationalError as e:
        print(f"SQLITE ERROR: {e}")
        print("Database is likely locked by another process.")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    main()
