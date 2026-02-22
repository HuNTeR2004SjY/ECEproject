
import requests
import sys

BASE_URL = "http://localhost:5000"

def test_login():
    print("Testing Login Flow...")
    s = requests.Session()
    
    # 1. Access Login Page
    try:
        r = s.get(f"{BASE_URL}/login")
        if r.status_code == 200 and "Login" in r.text:
            print("[PASS] Login page accessible")
        else:
            print(f"[FAIL] Login page access failed: {r.status_code}")
            return
    except Exception as e:
        print(f"[FAIL] Server not reachable: {e}")
        return

    # 2. Test Admin Login
    payload = {
        'company': 'TechCorp',
        'username': 'admin',
        'password': 'admin123'
    }
    r = s.post(f"{BASE_URL}/login", data=payload, allow_redirects=False)
    if r.status_code == 302 and 'admin/dashboard' in r.headers['Location']:
        print("[PASS] Admin login redirected to /admin/dashboard")
        
        # Follow redirect
        r = s.get(f"{BASE_URL}/admin/dashboard")
        if r.status_code == 200:
             print("[PASS] Admin dashboard accessible")
             if "Total Tickets" in r.text and "Active" in r.text:
                 print("[PASS] Stats widgets present")
             if "ticketDetailsModal" in r.text:
                 print("[PASS] Ticket details modal present")
             # regex check for stats numbers?
             # Just check if there is a number in the stat-value class
             if 'class="stat-value"' in r.text:
                  print("[PASS] Stats values rendered")
        else:
             print(f"[FAIL] Admin dashboard failed: {r.status_code}")
    else:
        print(f"[FAIL] Admin login failed. Status: {r.status_code}, Location: {r.headers.get('Location')}")

    # Logout
    s.get(f"{BASE_URL}/logout")
    
    # 3. Test Employee Login
    payload = {
        'company': 'TechCorp',
        'username': 'emp001',
        'password': 'user123'
    }
    r = s.post(f"{BASE_URL}/login", data=payload, allow_redirects=False)
    if r.status_code == 302 and '/index' in r.headers['Location'] or '/dashboard' in r.headers.get('Location', '') or 'next' in r.url: 
        # Note: app.py redirects to 'index' which might be / or /dashboard depending on logic
        # app.py says: return redirect(next_page or url_for('index')) which usually maps to '/'
        # And '/' redirects to '/dashboard'
        print(f"[PASS] Employee login redirected. Location: {r.headers.get('Location')}")
        
        # Follow to dashboard
        r = s.get(f"{BASE_URL}/dashboard")
        if r.status_code == 200:
            print("[PASS] Employee dashboard accessible")
        else:
            print(f"[FAIL] Employee dashboard failed: {r.status_code}")
    else:
        print(f"[FAIL] Employee login failed. Status: {r.status_code}, Location: {r.headers.get('Location')}")

    # 4. Test Unprotected API
    # /model-info is protected now
    r = requests.get(f"{BASE_URL}/model-info")
    if r.status_code == 401 or r.status_code == 302:
        print("[PASS] /model-info is protected")
    else:
        print(f"[FAIL] /model-info is NOT protected: {r.status_code}")

if __name__ == "__main__":
    test_login()
