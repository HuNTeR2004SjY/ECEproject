
import sys
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config

def test_email_connection():
    print("=" * 60)
    print("EMAIL DIAGNOSTIC")
    print("=" * 60)
    
    # 1. Print Config
    print("\n[Configuration]")
    print(f"Enabled: {config.EMAIL_CONFIG['enabled']}")
    print(f"SMTP Host: {config.EMAIL_CONFIG['smtp_host']}")
    print(f"SMTP Port: {config.EMAIL_CONFIG['smtp_port']}")
    print(f"SMTP User: {config.EMAIL_CONFIG['smtp_user']}")
    # Mask password
    pwd = config.EMAIL_CONFIG['smtp_password']
    print(f"SMTP Password: {'*' * (len(pwd) - 4) + pwd[-4:] if pwd else 'Not Set'}")
    print(f"From Email: {config.EMAIL_CONFIG['from_email']}")
    
    if not config.EMAIL_CONFIG['enabled']:
        print("\n[INFO] Email is disabled in configuration.")
        print("To enable, set env var ECE_EMAIL_ENABLED=true or update config.py")
        return

    # 2. Attempt Connection
    print("\n[Connection Test]")
    try:
        print(f"Connecting to {config.EMAIL_CONFIG['smtp_host']}:{config.EMAIL_CONFIG['smtp_port']}...")
        server = smtplib.SMTP(config.EMAIL_CONFIG['smtp_host'], config.EMAIL_CONFIG['smtp_port'])
        server.ehlo()
        server.starttls()
        server.ehlo()
        print("[OK] Connected and TLS established.")
        
        # 3. Login
        print("Logging in...")
        server.login(config.EMAIL_CONFIG['smtp_user'], config.EMAIL_CONFIG['smtp_password'])
        print("[OK] Login successful.")
        
        # 4. Send Test Email
        print("Sending test email...")
        msg = MIMEMultipart()
        msg['From'] = config.EMAIL_CONFIG['from_email']
        msg['To'] = config.EMAIL_CONFIG['from_email'] # Send to self
        msg['Subject'] = "ECE Email Diagnostic Test"
        msg.attach(MIMEText("This is a test email sent from the ECE diagnostic script.", 'plain'))
        
        server.send_message(msg)
        print(f"[OK] Email sent successfully to {msg['To']}")
        
        server.quit()
        
    except smtplib.SMTPAuthenticationError:
        print("[ERROR] SMTP Authentication Error: Invalid username or password.")
        print("   If using Gmail, verify App Password is correct and 2FA is enabled.")
    except Exception as e:
        print(f"[ERROR] Connection/Send Error: {e}")

if __name__ == "__main__":
    test_email_connection()
