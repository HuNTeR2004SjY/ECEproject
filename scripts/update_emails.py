
import sqlite3
import os
import sys

# Ensure we can find config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def update_emails():
    print(f"Updating database at: {config.DATABASE_PATH}")
    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # 1. Update Users
        print("\nUpdating Users...")
        # Admin
        cursor.execute("UPDATE users SET email = 'eceproject2026+admin@gmail.com' WHERE role = 'admin'")
        print(f"Updated admins: {cursor.rowcount}")
        
        # Employees (update all non-admins to employee email or specific if possible)
        # For simplicity and distinctness, let's use +employee for generic, but maybe +username would be better?
        # User requested: "use the plus address method to give this same email for all the needed emails"
        # Let's use +<username> for employees to keep them distinct but all going to same inbox
        
        cursor.execute("SELECT id, username FROM users WHERE role != 'admin'")
        users = cursor.fetchall()
        for user_id, username in users:
            new_email = f"eceproject2026+{username}@gmail.com"
            cursor.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
            print(f"Updated user {username} -> {new_email}")
            
        # 2. Update Departments
        print("\nUpdating Departments...")
        dept_map = {
            'IT': 'eceproject2026+it@gmail.com',
            'HR': 'eceproject2026+hr@gmail.com',
            'Facilities': 'eceproject2026+facilities@gmail.com',
            'Engineering': 'eceproject2026+engineering@gmail.com',
            'Legal': 'eceproject2026+legal@gmail.com',
            'Admin': 'eceproject2026+admin_dept@gmail.com'
        }
        
        cursor.execute("SELECT id, name FROM departments")
        depts = cursor.fetchall()
        for dept_id, name in depts:
            # Case insensitive match
            for key, email in dept_map.items():
                if key.lower() == name.lower():
                    cursor.execute("UPDATE departments SET email = ? WHERE id = ?", (email, dept_id))
                    print(f"Updated dept {name} -> {email}")
                    break
            else:
                # Fallback for unknown depts
                clean_name = name.lower().replace(' ', '_')
                email = f"eceproject2026+{clean_name}@gmail.com"
                cursor.execute("UPDATE departments SET email = ? WHERE id = ?", (email, dept_id))
                print(f"Updated generic dept {name} -> {email}")

        conn.commit()
        conn.close()
        print("\nUpdate complete!")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    update_emails()
