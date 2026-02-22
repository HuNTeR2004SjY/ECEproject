import sqlite3
import config

def migrate():
    print("Migrating database for Admin Features...")
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()

    # Create Departments Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        FOREIGN KEY (company_id) REFERENCES companies (id)
    )
    ''')
    print("Created 'departments' table.")

    # Seed initial departments if empty
    cursor.execute("SELECT COUNT(*) FROM departments")
    if cursor.fetchone()[0] == 0:
        print("Seeding default departments...")
        # Get TechCorp ID (assuming it's 1 or fetch it)
        cursor.execute("SELECT id FROM companies WHERE name='TechCorp'")
        company = cursor.fetchone()
        if company:
            company_id = company[0]
            default_depts = [
                ('IT', 'it-support@techcorp.com'),
                ('HR', 'hr@techcorp.com'),
                ('Facilities', 'facilities@techcorp.com'),
                ('Engineering', 'engineering@techcorp.com')
            ]
            for name, email in default_depts:
                cursor.execute("INSERT INTO departments (company_id, name, email) VALUES (?, ?, ?)", 
                               (company_id, name, email))
            print("Seeded default departments.")
    
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == '__main__':
    migrate()
