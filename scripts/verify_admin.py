import requests
import sys

BASE_URL = "http://localhost:5000"

def verify():
    session = requests.Session()
    
    # 1. Login
    print("Logging in as admin...")
    login_data = {
        "company": "TechCorp",
        "username": "admin",
        "password": "admin123"
    }
    response = session.post(f"{BASE_URL}/login", data=login_data)
    
    if "admin/dashboard" in response.url:
        print("Login successful!")
    else:
        print("Login failed!")
        print(response.text[:500])
        sys.exit(1)

    # 2. Get Users
    print("\nFetching users...")
    response = session.get(f"{BASE_URL}/api/admin/users")
    if response.status_code == 200:
        users = response.json().get('users', [])
        print(f"Found {len(users)} users.")
        for u in users:
            print(f"- {u['username']} ({u['role']})")
    else:
        print(f"Failed to fetch users: {response.status_code}")
        print(response.text)

    # 3. Add Department
    print("\nAdding 'Legal' department...")
    dept_data = {
        "name": "Legal",
        "email": "legal@techcorp.com"
    }
    response = session.post(f"{BASE_URL}/api/admin/departments", json=dept_data)
    if response.status_code == 200:
        print("Department added successfully.")
    else:
        print(f"Failed to add department: {response.status_code}")
        print(response.text)

    # 4. Get Departments
    print("\nFetching departments...")
    response = session.get(f"{BASE_URL}/api/admin/departments")
    if response.status_code == 200:
        depts = response.json().get('departments', [])
        print(f"Found {len(depts)} departments.")
        found_legal = False
        for d in depts:
            print(f"- {d['name']}: {d['email']}")
            if d['name'] == 'Legal':
                found_legal = True
        
        if found_legal:
            print("Verified: Legal department exists.")
        else:
            print("Error: Legal department not found in list.")
    else:
        print(f"Failed to fetch departments: {response.status_code}")
        print(response.text)

    # 5. Create User
    print("\nCreating new user 'legal_user'...")
    user_data = {
        "username": "legal_user",
        "password": "user123",
        "email": "legal_user@techcorp.com"
    }
    response = session.post(f"{BASE_URL}/api/admin/users", json=user_data)
    if response.status_code == 200:
        print("User created successfully.")
    else:
        print(f"Failed to create user: {response.status_code}")
        print(response.text)
        
    # 6. Verify New User
    print("\nVerifying new user in list...")
    response = session.get(f"{BASE_URL}/api/admin/users")
    users = response.json().get('users', [])
    found_user = any(u['username'] == 'legal_user' for u in users)
    if found_user:
        print("Verified: legal_user exists.")
    else:
        print("Error: legal_user not found.")

if __name__ == "__main__":
    import time
    max_retries = 30
    for i in range(max_retries):
        try:
            print(f"Attempt {i+1}/{max_retries} to connect...")
            verify()
            break
        except requests.exceptions.ConnectionError:
            print("Server not ready yet, waiting 2s...")
            time.sleep(2)
        except Exception as e:
            print(f"An error occurred: {e}")
            break
