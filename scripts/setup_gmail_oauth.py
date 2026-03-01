"""
Gmail OAuth2 One-Time Setup Script
====================================
Run this ONCE to authorize the ECE Agent to send emails via Gmail API.

Steps:
  1. Download credentials.json from Google Cloud Console and place it in the
     project root (E:\\College\\main pro\\VishnuSide\\ECE\\credentials.json)
  2. Run:  python scripts/setup_gmail_oauth.py
  3. A browser window will open → log in as eceproject2026@gmail.com → click Allow
  4. token.json is saved to the project root automatically
  5. Restart app.py — emails will now send via HTTPS (port 443)
"""

import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

SCOPES = ['https://www.googleapis.com/auth/gmail.send']
CREDENTIALS_FILE = os.path.join(PROJECT_ROOT, 'credentials.json')
TOKEN_FILE = os.path.join(PROJECT_ROOT, 'token.json')

def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Missing packages. Run:")
        print("  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\nERROR: credentials.json not found at:\n  {CREDENTIALS_FILE}\n")
        print("To get it:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Create a project > APIs & Services > Enable 'Gmail API'")
        print("  3. Credentials > Create > OAuth 2.0 Client ID > Desktop App")
        print("  4. Download JSON > rename to credentials.json > place in project root")
        sys.exit(1)

    creds = None

    # Load existing token if available
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token...")
            creds.refresh(Request())
        else:
            print("Opening browser for Gmail authorization...")
            print("Log in as: eceproject2026@gmail.com\n")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)

        # Save token
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        print(f"\n✅ token.json saved to: {TOKEN_FILE}")

    # Quick send test
    print("\nTesting Gmail API connection...")
    try:
        import base64
        from email.mime.text import MIMEText
        service = build('gmail', 'v1', credentials=creds)
        msg = MIMEText('<h2>Gmail API working!</h2><p>ECE Agent email is now operational via OAuth2.</p>', 'html')
        msg['Subject'] = 'ECE Agent - Gmail API Test'
        msg['From'] = 'eceproject2026@gmail.com'
        msg['To'] = 'eceproject2026@gmail.com'
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        print("✅ Test email sent successfully!")
        print("\nYou can now restart app.py — emails will send via Gmail API.")
    except Exception as e:
        print(f"❌ Send test failed: {e}")

if __name__ == '__main__':
    main()
