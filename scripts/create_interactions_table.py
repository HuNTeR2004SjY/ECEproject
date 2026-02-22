
import sqlite3
import os
import sys

# Ensure we can find config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def create_interactions_table():
    print(f"Updating database at: {config.DATABASE_PATH}")
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Create interactions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticket_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            sender TEXT NOT NULL,            -- 'user' or 'ai' (or 'human_agent')
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES classified_tickets (id)
        )
        ''')
        print("Created ticket_interactions table.")
        
        # Optional: Backfill interactions from existing tickets?
        # Only if we want to show current state as a "chat".
        # For now, let's just enable it for forward-moving conversations. 
        # But for the UI to look consistent, we might want to insert the *initial* body and *initial* solution as interactions 
        # for existing tickets if they aren't there.
        # Let's do a smart backfill: If a ticket has no interactions, insert Body and Solution (if solution exists).
        
        cursor.execute("SELECT id, body, status FROM classified_tickets")
        tickets = cursor.fetchall()
        
        count = 0
        for t_id, body, status in tickets:
            # Check if has interactions
            cursor.execute("SELECT COUNT(*) FROM ticket_interactions WHERE ticket_id = ?", (t_id,))
            if cursor.fetchone()[0] == 0:
                # 1. Insert User Message (Body)
                # We don't have exact timestamp of creation easily available here without query, so use NOW or NULL
                cursor.execute(
                    "INSERT INTO ticket_interactions (ticket_id, sender, message) VALUES (?, 'user', ?)",
                    (t_id, body)
                )
                
                # 2. Insert AI Solution (if exists, but we don't store the *text* of the solution in classified_tickets easily?)
                # Wait, we do not store the solution text in `classified_tickets`! 
                # We store predictions. But `learning_buffer` has `answer`.
                # Actually, `app.py` returns the solution in API but doesn't seem to persist the *generated text* in `classified_tickets`?
                # Checked schemas: `classified_tickets` has `pred_type`, `pred_priority`, etc. `body`... No `solution` column!
                # Ah, `app.py` logic: `response` dict has `result['solution']`.
                # DB insert: `INSERT INTO classified_tickets ...`. It inserts `status` but *not* the solution text?
                # This is a finding! We need to store the solution text to show it in history!
                # Wait, where does the dashboard get "Recommended Solution" from?
                # `window.SERVER_TICKETS` in `index.html`. `app.py`: `User.get_user_tickets`.
                # Let's check `User.get_user_tickets` query in `src/models.py`.
                pass
                
        # To fix the "Solution not saved" issue if it exists:
        # I should verify `src/models.py`. 
        # If solution isn't saved, I can't backfill it. 
        # Going forward, I should save it.
        
        # For now, just creating table.
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    create_interactions_table()
