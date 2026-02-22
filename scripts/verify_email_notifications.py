
import sys
import os
import logging
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.automation_specialist import AutomationSpecialist
import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_notifications():
    print("=" * 60)
    print("VERIFYING EMAIL NOTIFICATIONS")
    print("=" * 60)

    # Mock SMTP to capture emails
    with patch('smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        # Initialize Specialist
        specialist = AutomationSpecialist()
        
        # TEST 1: NOTIFY SOLUTION PROPOSED
        print("\n--------------------------------------------------")
        print("TEST 1: Solution Proposed Notification")
        print("--------------------------------------------------")
        
        ticket_data = {
            'id': 'TEST-SOL-001',
            'subject': 'Password Reset',
            'body': 'I cannot login',
            'type': 'incident',
            'priority': 'normal',
            'user_id': '123',
            'status': 'solution_proposed'
        }
        
        result = {
            'success': True,
            'solution': '1. Go to settings\n2. Click reset',
            'escalated': False
        }
        
        specialist.notify_ticket_resolution(ticket_data, result, 'user@example.com')
        
        # Verify call
        if mock_server.send_message.called:
            msg = mock_server.send_message.call_args[0][0]
            html = msg.get_payload()[0].get_payload()
            print("[OK] Email Sent")
            try:
                print("Subject:", msg['Subject'])
            except:
                print("Subject: [Unicode]")
            
            # Decode HTML
            try:
                # msg is multipart/relative? No, msg is the root. 
                # msg.get_payload()[0] should be the html part if it's the only one or first one?
                # Actually, MIMEMultipart('alternative') usually has text first, html second.
                # But my code attaches HTML only?
                # msg.attach(MIMEText(html_content, 'html'))
                # So it's the only part? No, loop through parts is safer.
                
                parts = msg.get_payload()
                html = ""
                for part in parts:
                    if part.get_content_type() == 'text/html':
                        html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                
                if not html:
                     # Fallback if structure is different
                     html = parts[0].get_payload(decode=True).decode('utf-8', errors='ignore')

            except Exception as e:
                print(f"[WARN] Failed to decode HTML: {e}")
                html = ""

            # Print HTML for debugging (safely)
            print("--- HTML CONTENT START ---")
            print(html.encode('ascii', 'ignore').decode('ascii'))
            print("--- HTML CONTENT END ---")

            # Check content
            if "Action Required" in html and "Go to settings" in html:
                print("[OK] Content verified: Contains solution steps")
            else:
                print("[FAIL] Content verification failed")
        else:
            print("[FAIL] Email NOT sent")

        # TEST 2: NOTIFY ESCALATION
        print("\n--------------------------------------------------")
        print("TEST 2: Escalation Notification")
        print("--------------------------------------------------")
        
        # Reset mock
        mock_server.reset_mock()
        
        ticket_escalated = {
            'id': 'TEST-ESC-001',
            'subject': 'Server Fire',
            'body': 'Smoke coming from server room',
            'type': 'incident',
            'priority': 'critical',
            'user_id': '123',
            'status': 'escalated'
        }
        
        # Escalation result usually comes from ProblemSolver
        result_esc = {
            'success': False,
            'escalated': True,
            'escalation_reason': 'Physical hazard detected',
            'sla_minutes': 15 
        }
        
        # Note: notify_ticket_resolution calls escalate_ticket -> _notify_user_escalation -> send_email
        specialist.notify_ticket_resolution(ticket_escalated, result_esc, 'user@example.com')
        
        # Verify calls (should send 2 emails: 1 to dept, 1 to user)
        # We focus on the user email here
        call_count = mock_server.send_message.call_count
        print(f"Emails sent: {call_count}")
        
        found_user_email = False
        for call in mock_server.send_message.call_args_list:
            msg = call[0][0]
            if msg['To'] == 'user@example.com':
                found_user_email = True
                
                # Decode HTML
                try:
                    parts = msg.get_payload()
                    html = ""
                    for part in parts:
                        if part.get_content_type() == 'text/html':
                            html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                    if not html:
                        html = parts[0].get_payload(decode=True).decode('utf-8', errors='ignore')
                except Exception as e:
                     print(f"[WARN] Failed to decode: {e}")
                     html = ""

                print("[OK] User Email Sent")
                try:
                    print("Subject:", msg['Subject'])
                except:
                    print("Subject: [Unicode]")
                
                # Print HTML for debugging (safely)
                print("--- HTML CONTENT START ---")
                print(html.encode('ascii', 'ignore').decode('ascii'))
                print("--- HTML CONTENT END ---")
                
                # Check content for Department and SLA
                # Department might be FACILITIES or IT based on routing logic for 'Smoke'
                if "Expected Resolution" in html:
                    print("[OK] Content verified: Contains SLA/Resolution expectation")
                else:
                    print("[FAIL] Content verification failed (SLA missing)")
                    
                if "Escalated to" in html:
                    print("[OK] Content verified: Contains Department info")
        
        if not found_user_email:
            print("[FAIL] User email NOT sent")

if __name__ == "__main__":
    verify_notifications()
