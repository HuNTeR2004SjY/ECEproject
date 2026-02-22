
import requests
import json
import time

BASE_URL = "http://localhost:5000"
# Login first
session = requests.Session()

def login():
    print("Logging in...")
    # Assuming default admin credentials or test user
    # If using test user: emp001/password123
    try:
        # 1. Get CSRF Token
        login_page = session.get(f"{BASE_URL}/login")
        import re
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        csrf_token = csrf_match.group(1) if csrf_match else None
        
        if not csrf_token:
            print("Warning: Could not find CSRF token. Proceeding without it (might fail).")
            
        response = session.post(f"{BASE_URL}/login", data={
            'csrf_token': csrf_token,
            'company': 'TechCorp',
            'username': 'testuser',
            'password': 'testpass'
        })
        
        if response.status_code == 200 and "Log In" not in response.text:
            # Check if we were redirected or if login page text is gone
            # Note: requests follows redirects by default. 
            # If successful, we should be at dashboard (index) or admin dashboard.
            print("Login successful")
            return True
        elif response.url.endswith('/login'):
             print("Login failed: Still on login page")
             return False
        else:
             print(f"Login outcome uncertain: {response.status_code} at {response.url}")
             return True # Assume success if redirected
             
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

def test_conversation():
    # 1. Create Ticket
    print("\n1. Creating Ticket...")
    ticket_data = {
        'subject': 'Help with VPN connection',
        'body': 'I cannot connect to the VPN from home.'
    }
    
    try:
        response = session.post(f"{BASE_URL}/predict", json=ticket_data)
        if response.status_code != 200:
            print(f"Failed to create ticket: {response.text}")
            return
            
        data = response.json()
        ticket_id = data['ticket_id']
        print(f"Ticket Created: {ticket_id}")
        print(f"Initial AI Solution: {data['solution'][:50]}...")
        
        # 2. Get Details
        print("\n2. Fetching Details...")
        response = session.get(f"{BASE_URL}/api/ticket/{ticket_id}/details")
        if response.status_code != 200:
            print(f"Failed to get details: {response.text}")
            return
            
        details = response.json()
        interactions = details['interactions']
        print(f"Interactions count: {len(interactions)}")
        for msg in interactions:
            print(f"  - {msg['sender']}: {msg['message'][:30]}...")
            
        # 3. Reply to Ticket
        print("\n3. Sending Reply...")
        reply_data = {'message': "I already tried restarting my router but it didn't work."}
        response = session.post(f"{BASE_URL}/api/ticket/{ticket_id}/reply", json=reply_data)
        
        if response.status_code != 200:
            print(f"Failed to reply: {response.text}")
            return
            
        reply_res = response.json()
        print(f"Reply Success: {reply_res['success']}")
        print(f"AI Response: {reply_res['reply'][:100]}...")
        
        # 4. Verify History Update
        print("\n4. Verifying History...")
        response = session.get(f"{BASE_URL}/api/ticket/{ticket_id}/details")
        updated_details = response.json()
        updated_interactions = updated_details['interactions']
        print(f"Updated Interactions count: {len(updated_interactions)}")
        
        if len(updated_interactions) >= 4: # User, AI, User, AI
            print("✅ Conversation flow verified successfully!")
        else:
            print("❌ History count mismatch!")

    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    if login():
        test_conversation()
