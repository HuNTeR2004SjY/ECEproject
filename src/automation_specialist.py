"""
AUTOMATION SPECIALIST
=====================

The final execution layer in the ECE pipeline. Handles:
- User notifications (pop-ups and emails)
- Solution execution (automated or guided)
- Human escalation routing
- Status tracking and monitoring

Author: ECE Team
Date: February 2026
"""

import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any
import subprocess
import re
from pathlib import Path
import sqlite3

import config
from src.groq_email_generator import GroqEmailGenerator
# from src.google_genai_email import GoogleGenAIEmailGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# GMAIL API SENDER (OAuth2 via HTTPS -- works on all networks)
# ============================================================================

class GmailAPIEmailSender:
    """
    Sends emails via the Gmail REST API using a saved OAuth2 token.
    Works over HTTPS (port 443) -- never blocked on campus/corporate networks.

    Prerequisites:
      1. Run scripts/setup_gmail_oauth.py once to generate token.json
      2. token.json must be placed in the project root directory
    """

    SCOPES = ['https://www.googleapis.com/auth/gmail.send']
    TOKEN_PATH = str(Path(config.PROJECT_DIR) / 'token.json')
    CREDENTIALS_PATH = str(Path(config.PROJECT_DIR) / 'credentials.json')

    def __init__(self):
        self._service = None   # lazy-loaded
        self.available = self._check_available()

    def _check_available(self) -> bool:
        """Return True if token.json exists and packages are installed."""
        if not Path(self.TOKEN_PATH).exists():
            logger.info("GmailAPI: token.json not found -- SMTP fallback will be used")
            return False
        try:
            import googleapiclient  # noqa
            import google.oauth2.credentials  # noqa
            return True
        except ImportError:
            logger.warning("GmailAPI: google-api-python-client not installed -- SMTP fallback will be used")
            return False

    def _get_service(self):
        """Load / refresh credentials and return the Gmail API service object."""
        if self._service:
            return self._service
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(self.TOKEN_PATH, self.SCOPES)
        if creds.expired and creds.refresh_token:
            logger.info("GmailAPI: refreshing access token...")
            creds.refresh(Request())
            with open(self.TOKEN_PATH, 'w') as f:
                f.write(creds.to_json())
        self._service = build('gmail', 'v1', credentials=creds)
        return self._service

    def send(self, msg) -> bool:
        """
        Send a MIMEMultipart message object via Gmail API.
        Returns True on success, False on failure.
        """
        import base64
        try:
            service = self._get_service()
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId='me', body={'raw': raw}).execute()
            return True
        except Exception as e:
            logger.error(f"GmailAPI send failed: {e}")
            self._service = None   # reset so next attempt re-authenticates
            return False


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class SolutionType(Enum):
    """Types of solutions and their execution methods"""
    AUTOMATED = "automated"   # Fully automated - system executes
    GUIDED = "guided"         # Semi-automated - user follows steps
    MANUAL = "manual"         # Requires human expertise


class TicketStatus(Enum):
    """All possible ticket states"""
    CREATED = "created"
    TRIAGED = "triaged"
    PROCESSING = "processing"
    SOLUTION_PROPOSED = "solution_proposed"
    QUALITY_CHECK = "quality_check"
    APPROVED = "approved"
    EXECUTING = "executing"
    AWAITING_USER = "awaiting_user"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class NotificationChannel(Enum):
    """Notification delivery channels"""
    POPUP = "popup"
    EMAIL = "email"
    SMS = "sms"


# Department routing configuration
DEPARTMENT_ROUTING = {
    'IT': {
        'types': ['incident', 'problem'],
        'keywords': ['password', 'login', 'access', 'vpn', 'network', 'email', 'software'],
        'email': 'eceproject2026+it@gmail.com',
        'escalation_sla': 30  # minutes
    },
    'HR': {
        'types': ['request'],
        'keywords': ['leave', 'payroll', 'benefits', 'onboarding', 'performance'],
        'email': 'eceproject2026+hr@gmail.com',
        'escalation_sla': 60
    },
    'FACILITIES': {
        'types': ['incident', 'request'],
        'keywords': ['printer', 'desk', 'office', 'parking', 'building', 'maintenance'],
        'email': 'eceproject2026+facilities@gmail.com',
        'escalation_sla': 120
    },
    'ENGINEERING': {
        'types': ['problem', 'incident'],
        'keywords': ['bug', 'error', 'crash', 'performance', 'api', 'database', 'server'],
        'email': 'eceproject2026+engineering@gmail.com',
        'escalation_sla': 15
    }
}


# ============================================================================
# NOTIFICATION ENGINE
# ============================================================================

class NotificationManager:
    """
    Manages all types of notifications (pop-ups, emails, SMS)
    """
    
    def __init__(self, email_config: Optional[Dict] = None):
        """
        Initialize notification manager
        
        Args:
            email_config: SMTP configuration for email sending
        """
        self.email_config = email_config or config.EMAIL_CONFIG
        self.notification_history = []

        # Gmail API sender (OAuth2 over HTTPS -- preferred over SMTP)
        self.gmail_api = GmailAPIEmailSender()

        # Initialize AI Email Generator (Groq)
        self.ai_email_generator = GroqEmailGenerator()
        if self.ai_email_generator.enabled:
            logger.info("AI Email Generation Enabled (Groq)")
        else:
            logger.info("AI Email Generation Disabled (using templates)")
            
        logger.info("NotificationManager initialized")
    
    def send_notification(
        self,
        ticket: Dict,
        event_type: str,
        channels: List[NotificationChannel],
        user_email: str,
        user_id: str
    ) -> Dict:
        """
        Send notification across specified channels
        
        Args:
            ticket: Ticket object
            event_type: Type of event (created, status_change, solution_ready, etc.)
            channels: List of channels to use
            user_email: User's email address
            user_id: User's ID
            
        Returns:
            Dict with send results per channel
        """
        results = {}
        
        # Create notification content
        notification = self._create_notification(ticket, event_type)
        
        for channel in channels:
            try:
                if channel == NotificationChannel.EMAIL:
                    result = self._send_email(notification, user_email, ticket)
                    results['email'] = result
                    
                elif channel == NotificationChannel.POPUP:
                    result = self._create_popup_data(notification, ticket)
                    results['popup'] = result
                    
                elif channel == NotificationChannel.SMS:
                    result = self._send_sms(notification, user_email, ticket)
                    results['sms'] = result
                    
            except Exception as e:
                logger.error(f"Failed to send {channel.value} notification: {e}")
                results[channel.value] = {'success': False, 'error': str(e)}
        
        # Log notification
        self._log_notification(ticket, event_type, channels, results)
        
        return results
    
    def _create_notification(self, ticket: Dict, event_type: str) -> Dict:
        """Create notification content based on event type"""
        
        notifications = {
            'ticket_created': {
                'title': f"✓ Ticket #{ticket['id']} Created",
                'message': f"We've received your request: {ticket['subject']}",
                'priority': 'normal'
            },
            'triaged': {
                'title': f"Ticket #{ticket['id']} Classified",
                'message': f"Your ticket has been classified as {ticket.get('type', 'N/A')} with {ticket.get('priority', 'normal')} priority",
                'priority': 'normal'
            },
            'processing': {
                'title': f"Working on Ticket #{ticket['id']}",
                'message': f"Our AI is analyzing your issue: {ticket['subject']}",
                'priority': 'normal'
            },
            'solution_proposed': {
                'title': f"💡 Solution Found for #{ticket['id']}",
                'message': "We've found a solution! Review it in your dashboard.",
                'priority': 'high'
            },
            'quality_check': {
                'title': f"Validating Solution for #{ticket['id']}",
                'message': "Solution is being validated for safety and quality...",
                'priority': 'normal'
            },
            'approved': {
                'title': f"✅ Solution Approved for #{ticket['id']}",
                'message': "Solution approved! Preparing for execution...",
                'priority': 'high'
            },
            'executing': {
                'title': f"⚙️ Executing Solution for #{ticket['id']}",
                'message': "We're applying the solution now...",
                'priority': 'high'
            },
            'completed': {
                'title': f"🎉 Ticket #{ticket['id']} Resolved!",
                'message': f"Your issue has been successfully resolved!",
                'priority': 'high'
            },
            'escalated': {
                'title': f"👥 Ticket #{ticket['id']} Escalated",
                'message': "Your ticket has been escalated to our expert team",
                'priority': 'high'
            },
            'awaiting_user': {
                'title': f"Action Required: Ticket #{ticket['id']}",
                'message': "Please follow the provided steps to resolve your issue",
                'priority': 'high'
            },
            'sla_warning': {
                'title': f"⚠️ SLA Alert: Ticket #{ticket['id']}",
                'message': "Your ticket is approaching its resolution deadline",
                'priority': 'urgent'
            }
        }
        
        notification = notifications.get(event_type, {
            'title': f"Update: Ticket #{ticket['id']}",
            'message': f"Status: {ticket.get('status', 'unknown')}",
            'priority': 'normal'
        })
        
        notification['timestamp'] = datetime.now().isoformat()
        notification['ticket_id'] = ticket['id']
        notification['event_type'] = event_type
        
        # Capture context for templates
        if 'escalated_to_dept' in ticket:
            notification['department'] = ticket['escalated_to_dept']
            notification['sla_minutes'] = ticket.get('escalated_sla', 60)
            
        return notification
    
    def _send_email(self, notification: Dict, user_email: str, ticket: Dict) -> Dict:
        """
        Send email notification.
        Priority order:
          1. Gmail API (OAuth2 over HTTPS) -- if token.json exists
          2. SMTP_SSL port 465                -- background thread, 10s timeout
          3. STARTTLS port 587                -- background thread, 10s timeout
        """
        import threading, ssl as _ssl

        try:
            # --- Build the email message ---
            msg = MIMEMultipart('alternative')
            msg['Subject'] = notification['title']
            msg['From'] = self.email_config.get('from_email', 'eceproject2026+noreply@gmail.com')
            msg['To'] = user_email

            if self.ai_email_generator.enabled and notification.get('priority') != 'urgent':
                try:
                    ai_body = self.ai_email_generator.generate_email_content(ticket, context=notification['message'])
                    html_content = self._generate_email_html(notification, ticket, ai_body=ai_body)
                except Exception as e:
                    logger.error(f"AI Email generation failed: {e}")
                    html_content = self._generate_email_html(notification, ticket)
            else:
                html_content = self._generate_email_html(notification, ticket)

            msg.attach(MIMEText(html_content, 'html'))

            if not self.email_config.get('enabled', False):
                logger.info(f"Email disabled - would send to {user_email}")
                return {'success': True, 'sent_at': datetime.now().isoformat(), 'note': 'Email disabled in config'}

            # --- Attempt 1: Gmail API (HTTPS, never blocked) ---
            if self.gmail_api.available:
                def _api_send():
                    ok = self.gmail_api.send(msg)
                    if ok:
                        logger.info(f"Email sent via Gmail API to {user_email} for ticket {ticket['id']}")
                    else:
                        logger.warning(f"Gmail API failed for {user_email}, falling back to SMTP...")
                        self._smtp_send(msg, user_email, ticket)
                threading.Thread(target=_api_send, daemon=True).start()
                return {'success': True, 'sent_at': datetime.now().isoformat(), 'note': 'Sending via Gmail API'}

            # --- Attempt 2 & 3: SMTP fallback (background thread) ---
            threading.Thread(target=self._smtp_send, args=(msg, user_email, ticket), daemon=True).start()
            return {'success': True, 'sent_at': datetime.now().isoformat(), 'note': 'Sending via SMTP in background'}

        except Exception as e:
            logger.error(f"Failed to prepare email: {e}")
            return {'success': False, 'error': str(e)}

    def _smtp_send(self, msg, user_email, ticket):
        smtp_host = self.email_config['smtp_host']
        smtp_user = self.email_config['smtp_user']
        smtp_pass = self.email_config['smtp_password']
        TIMEOUT = 10

        # Try SSL:465
        import ssl as _ssl
        try:
            ctx = _ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, 465, context=ctx, timeout=TIMEOUT) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            logger.info(f"Email sent (SSL:465) to {user_email} for ticket {ticket['id']}")
            return
        except Exception as e1:
            logger.warning(f"SSL:465 failed ({e1}), trying STARTTLS:587...")
        # Try STARTTLS:587
        try:
            with smtplib.SMTP(smtp_host, 587, timeout=TIMEOUT) as s:
                s.ehlo(); s.starttls(); s.ehlo()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            logger.info(f"Email sent (STARTTLS:587) to {user_email} for ticket {ticket['id']}")
        except Exception as e2:
            logger.error(f"STARTTLS:587 also failed: {e2}")

        except Exception as e:
            logger.error(f"Failed to prepare email: {e}")
            return {'success': False, 'error': str(e)}
    
    def _generate_email_html(self, notification: Dict, ticket: Dict, ai_body: Optional[str] = None) -> str:
        """Generate HTML email content"""
        
        priority_colors = {
            'urgent': '#dc2626',
            'high': '#ea580c',
            'normal': '#2563eb',
            'low': '#059669'
        }
        
        color = priority_colors.get(notification.get('priority', 'normal'), '#2563eb')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ 
                    background: {color}; 
                    color: white; 
                    padding: 20px; 
                    border-radius: 8px 8px 0 0; 
                }}
                .content {{ 
                    background: #f9fafb; 
                    padding: 20px; 
                    border: 1px solid #e5e7eb;
                }}
                .ticket-info {{ 
                    background: white; 
                    padding: 15px; 
                    margin: 15px 0; 
                    border-left: 4px solid {color};
                }}
                .action-button {{ 
                    background: {color}; 
                    color: white; 
                    padding: 12px 24px; 
                    text-decoration: none; 
                    border-radius: 6px; 
                    display: inline-block; 
                    margin: 20px 0;
                }}
                .footer {{ 
                    background: #f3f4f6; 
                    padding: 15px; 
                    text-align: center; 
                    font-size: 12px; 
                    color: #6b7280;
                    border-radius: 0 0 8px 8px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>{notification['title']}</h2>
                </div>
                
                <div class="content">
                    {ai_body if ai_body else f"<p>{notification['message']}</p>"}
                    
                    <div class="ticket-info">
                        <p><strong>Ticket ID:</strong> {ticket['id']}</p>
                        <p><strong>Subject:</strong> {ticket.get('subject', 'N/A')}</p>
                        <p><strong>Status:</strong> {ticket.get('status', 'unknown')}</p>
                        <p><strong>Priority:</strong> {ticket.get('priority', 'normal')}</p>
                    </div>

                    <!-- RESOLUTION DETAILS SECTION -->
                    <div style="background: #eef2ff; padding: 15px; border-radius: 6px; margin: 15px 0; border: 1px solid #c7d2fe;">
                        <h3 style="color: #4338ca; margin-top: 0;">Resolution Details</h3>
                        
                        {f'<p><strong>❌ Escalated to:</strong> {notification.get("department", "Support Team")}</p><p><strong>⏳ Expected Resolution:</strong> Within {notification.get("sla_minutes", "60")} minutes</p><p>We have forwarded your request to the specialized department better suited to handle this complex issue.</p>' if notification.get('event_type') == 'escalated' else ''}
                        
                        {f'<p><strong>✅ Action Required:</strong> You can resolve this issue yourself!</p><p><strong>Solution:</strong></p><pre style="white-space: pre-wrap; font-family: inherit; background: white; padding: 10px; border-radius: 4px; border: 1px solid #e5e7eb;">{ticket.get("solution_text", ticket.get("body", ""))}</pre>' if notification.get('event_type') == 'solution_proposed' else ''}
                    </div>
                    
                    <p style="text-align: center;">
                        {f'''
                        <div style="margin-top: 20px; padding: 15px; background: #fff; border-radius: 6px; text-align: center;">
                            <h4 style="margin-top:0;">Did this solve your issue?</h4>
                            <a href="{config.APP_URL}/ticket/confirm/{ticket['id']}?response=yes" style="display: inline-block; padding: 10px 20px; background-color: #10b981; color: white; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">Yes, my issue is resolved</a>
                            <a href="{config.APP_URL}/ticket/confirm/{ticket['id']}?response=no" style="display: inline-block; padding: 10px 20px; background-color: #ef4444; color: white; text-decoration: none; border-radius: 4px; font-weight: bold;">No, I still need help</a>
                        </div>
                        ''' if notification.get('event_type') == 'solution_proposed' else f'''
                        <a href="{config.APP_URL}/ticket/{ticket['id']}" class="action-button">
                            View Ticket Details
                        </a>
                        '''}
                    </p>
                </div>
                
                <div class="footer">
                    <p>Enterprise Context Engine - Automated Support System</p>
                    <p>Questions? Contact {config.SUPPORT_EMAIL}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def _create_popup_data(self, notification: Dict, ticket: Dict) -> Dict:
        """
        Create pop-up notification data for frontend
        
        Returns:
            Dict with pop-up configuration
        """
        return {
            'success': True,
            'popup': {
                'title': notification['title'],
                'message': notification['message'],
                'priority': notification['priority'],
                'ticket_id': ticket['id'],
                'timestamp': notification['timestamp'],
                'actions': [
                    {
                        'label': 'View Details',
                        'url': f"/ticket/{ticket['id']}",
                        'type': 'primary'
                    },
                    {
                        'label': 'Dismiss',
                        'type': 'secondary'
                    }
                ]
            }
        }
    
    def _send_sms(self, notification: Dict, phone: str, ticket: Dict) -> Dict:
        """Send SMS notification (placeholder for Twilio integration)"""
        logger.info(f"SMS notification would be sent to {phone}")
        return {
            'success': True,
            'note': 'SMS not implemented - requires Twilio configuration'
        }
    
    def _log_notification(
        self,
        ticket: Dict,
        event_type: str,
        channels: List[NotificationChannel],
        results: Dict
    ):
        """Log notification for audit trail"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'ticket_id': ticket['id'],
            'event_type': event_type,
            'channels': [c.value for c in channels],
            'results': results
        }
        self.notification_history.append(log_entry)


# ============================================================================
# EXECUTION ENGINE
# ============================================================================

class ExecutionEngine:
    """
    Determines and executes approved solutions
    """
    
    def __init__(self):
        """Initialize execution engine"""
        self.execution_history = []
        logger.info("ExecutionEngine initialized")
    
    def execute_solution(self, ticket: Dict, approved_solution: Dict) -> Dict:
        """
        Execute the approved solution based on its type
        
        Args:
            ticket: Ticket object
            approved_solution: Solution approved by Quality Gatekeeper
            
        Returns:
            ExecutionResult with status and details
        """
        logger.info(f"Executing solution for ticket {ticket['id']}")
        
        # Classify solution type
        solution_type = self._classify_solution(approved_solution)
        
        result = {
            'ticket_id': ticket['id'],
            'solution_type': solution_type.value,
            'execution_status': 'pending',
            'started_at': datetime.now().isoformat(),
            'details': {}
        }
        
        try:
            if solution_type == SolutionType.AUTOMATED:
                result.update(self._execute_automated(ticket, approved_solution))
                
            elif solution_type == SolutionType.GUIDED:
                result.update(self._generate_user_guide(ticket, approved_solution))
                
            elif solution_type == SolutionType.MANUAL:
                result.update(self._escalate_to_human(ticket, approved_solution))
            
            result['execution_status'] = 'completed'
            result['completed_at'] = datetime.now().isoformat()
            
        except Exception as e:
            logger.error(f"Execution failed for {ticket['id']}: {e}")
            result['execution_status'] = 'failed'
            result['error'] = str(e)
            # Fallback to manual escalation
            result.update(self._escalate_to_human(ticket, approved_solution, error=e))
        
        # Log execution
        self.execution_history.append(result)
        
        return result
    
    def _classify_solution(self, solution: Dict) -> SolutionType:
        """
        Determine if solution can be automated or needs user action
        
        Returns:
            SolutionType enum value
        """
        solution_text = solution.get('text', '').lower()
        metadata = solution.get('metadata', {})
        
        # Check metadata for explicit type
        if 'solution_type' in metadata:
            return SolutionType(metadata['solution_type'])
        
        # Check for automation indicators
        automation_indicators = [
            'api_call', 'script_execute', 'database_update',
            'permission_grant', 'account_reset', 'service_restart',
            'automated', 'execute'
        ]
        
        manual_indicators = [
            'manually', 'contact', 'please', 'you should',
            'go to', 'click', 'open', 'navigate', 'follow these steps'
        ]
        
        # Score each type
        automation_score = sum(1 for indicator in automation_indicators if indicator in solution_text)
        manual_score = sum(1 for indicator in manual_indicators if indicator in solution_text)
        
        # Decision logic
        if automation_score > manual_score and automation_score >= 2:
            return SolutionType.AUTOMATED
        elif manual_score > 0:
            return SolutionType.GUIDED
        else:
            # Default to guided for safety
            return SolutionType.GUIDED
    
    def _execute_automated(self, ticket: Dict, solution: Dict) -> Dict:
        """
        Execute fully automated solution
        
        Note: This is a placeholder. Actual implementation would:
        - Call APIs (password reset, permission grants, etc.)
        - Execute safe scripts
        - Update configurations
        """
        logger.info(f"Executing automated solution for {ticket['id']}")
        
        # Placeholder for actual automation
        return {
            'method': 'automated',
            'action': 'automated_execution',
            'success': True,
            'message': 'Solution executed automatically',
            'note': 'Automation framework placeholder - would execute actual APIs/scripts here'
        }
    
    def _generate_user_guide(self, ticket: Dict, solution: Dict) -> Dict:
        """
        Generate step-by-step user guide
        
        Args:
            ticket: Ticket object
            solution: Solution dict with text
            
        Returns:
            Dict with formatted guide
        """
        logger.info(f"Generating user guide for {ticket['id']}")
        
        # Parse solution into steps
        steps = self._parse_solution_steps(solution.get('text', ''))
        
        # Enrich steps
        enriched_steps = self._enrich_steps(steps, ticket)
        
        guide = {
            'method': 'user_guided',
            'title': f"How to Resolve: {ticket.get('subject', 'Your Issue')}",
            'steps': enriched_steps,
            'estimated_time': self._estimate_completion_time(enriched_steps),
            'difficulty': self._assess_difficulty(enriched_steps),
            'total_steps': len(enriched_steps),
            'support_link': f"{config.APP_URL}/ticket/{ticket['id']}"
        }
        
        return guide
    
    def _parse_solution_steps(self, solution_text: str) -> List[Dict]:
        """Parse solution text into discrete steps"""
        
        # Try numbered format: "1. Step one\n2. Step two"
        numbered_pattern = r'(\d+)\.\s+(.+?)(?=\d+\.|$)'
        matches = re.findall(numbered_pattern, solution_text, re.DOTALL)
        
        if matches:
            return [
                {
                    'step_number': int(num),
                    'instruction': step.strip()
                }
                for num, step in matches
            ]
        
        # Try bullet format: "- Step one\n- Step two"
        bullet_pattern = r'[-•]\s+(.+?)(?=[-•]|$)'
        matches = re.findall(bullet_pattern, solution_text, re.DOTALL)
        
        if matches:
            return [
                {
                    'step_number': i + 1,
                    'instruction': step.strip()
                }
                for i, step in enumerate(matches)
            ]
        
        # Fallback: Split by newlines
        lines = [line.strip() for line in solution_text.split('\n') if line.strip()]
        return [
            {
                'step_number': i + 1,
                'instruction': line
            }
            for i, line in enumerate(lines)
        ]
    
    def _enrich_steps(self, steps: List[Dict], ticket: Dict) -> List[Dict]:
        """Add helpful context to each step"""
        enriched = []
        
        for step in steps:
            enriched_step = step.copy()
            
            # Estimate step duration
            instruction = step['instruction'].lower()
            if any(word in instruction for word in ['wait', 'restart', 'reboot']):
                enriched_step['estimated_duration'] = '2-5 minutes'
            elif any(word in instruction for word in ['download', 'install']):
                enriched_step['estimated_duration'] = '5-10 minutes'
            else:
                enriched_step['estimated_duration'] = '1-2 minutes'
            
            # Add quick link if applicable
            if 'settings' in instruction:
                enriched_step['quick_link'] = f"{config.APP_URL}/settings"
            
            enriched.append(enriched_step)
        
        return enriched
    
    def _estimate_completion_time(self, steps: List[Dict]) -> str:
        """Estimate total completion time"""
        total_minutes = len(steps) * 2  # Rough estimate: 2 min per step
        
        if total_minutes < 5:
            return "< 5 minutes"
        elif total_minutes < 15:
            return "5-15 minutes"
        elif total_minutes < 30:
            return "15-30 minutes"
        else:
            return "30+ minutes"
    
    def _assess_difficulty(self, steps: List[Dict]) -> str:
        """Assess difficulty level"""
        num_steps = len(steps)
        
        if num_steps <= 3:
            return "Easy"
        elif num_steps <= 7:
            return "Medium"
        else:
            return "Complex"
    
    def _escalate_to_human(
        self,
        ticket: Dict,
        solution: Dict,
        error: Optional[Exception] = None
    ) -> Dict:
        """
        Escalate to human team (handled by EscalationManager)
        """
        logger.info(f"Solution requires manual escalation for {ticket['id']}")
        
        return {
            'method': 'manual_escalation',
            'requires_human': True,
            'reason': str(error) if error else 'Complex issue requiring human expertise',
            'message': 'This issue has been escalated to our expert team'
        }


# ============================================================================
# ESCALATION MANAGER
# ============================================================================

class EscalationManager:
    """
    Handles human escalations with intelligent Smart Escalation routing.
    Scores available HumanTeamMembers based on workload, skill match, and SLA urgency.
    Fires Email, Slack, and Jira notifications via per-company integrations.
    """
    
    def __init__(self, notification_manager: NotificationManager):
        self.notification_manager = notification_manager
        self.escalation_history = []
        logger.info("EscalationManager initialized")

    def escalate_ticket(self, ticket: Dict, reason: str, user_email: str) -> Dict:
        logger.info(f"Escalating ticket {ticket['id']}: {reason}")
        from src.models import HumanTeamMember, CompanySettings
        from datetime import datetime, timezone
        
        company_id = ticket.get('company_id')
        if not company_id:
            logger.warning(f"No company_id for ticket {ticket['id']}, cannot route to team member.")
            return {'department': 'Fallback', 'reason': reason, 'priority': 'normal'}
            
        queue = ticket.get('type', ticket.get('queue', 'General'))
        priority = ticket.get('priority', 'Medium')
        
        # 1. Score Members
        members = HumanTeamMember.get_available(company_id)
        open_counts = HumanTeamMember._open_ticket_counts(company_id)
        
        scored = []
        MAX_WORKLOAD = 20
        for m in members:
            open_tickets = open_counts.get(m['id'], 0)
            if open_tickets >= MAX_WORKLOAD:
                continue
            wl_score = 1.0 - (open_tickets / MAX_WORKLOAD)
            
            # Skill score
            skills_list = [s.strip().lower() for s in m['skills'].split(",")]
            skill_score = 1.0 if queue.lower() in skills_list else 0.5 # Simplified keyword overlap
            
            # SLA score
            urgency_score = {"High": 1.0, "Medium": 0.5, "Low": 0.2}.get(priority, 0.3)
            
            composite = round(0.60 * wl_score + 0.30 * skill_score + 0.10 * urgency_score, 4)
            scored.append({
                "id": m["id"],
                "name": m["name"],
                "email": m["email"],
                "role": m["role"],
                "skills": m["skills"],
                "open_tickets": open_tickets,
                "workload_score": wl_score,
                "skill_score": skill_score,
                "urgency_score": urgency_score,
                "composite_score": composite,
            })
            
        scored.sort(key=lambda x: x["composite_score"], reverse=True)
        
        if not scored:
            return {'department': 'Fallback', 'reason': 'No eligible agents', 'priority': priority}
            
        top = scored[0]
        runner = scored[1] if len(scored) > 1 else None
        
        # Assign
        HumanTeamMember.record_assignment(company_id, ticket['id'], top['id'])
        
        routing = {
            "recommended_agent": top,
            "runner_up": runner,
            "reasoning": f"{top['name']} recommended (Score: {top['composite_score']:.0%})."
        }
        
        # 2. Notify over channels independently
        notifications_result = self._notify_all_channels(company_id, ticket, routing)
        
        escalation = {
            'ticket_id': ticket['id'],
            'department': top['name'],
            'reason': reason,
            'escalated_at': datetime.now().isoformat(),
            'priority': priority,
            'routing': routing,
            'notifications': notifications_result
        }
        self.escalation_history.append(escalation)
        
        # Notify user (Fallback popup via UI stream, but we can standard email them)
        try:
            ticket_with_context = ticket.copy()
            ticket_with_context['escalated_to_dept'] = top['name']
            ticket_with_context['escalated_sla'] = 60
            self.notification_manager.send_notification(
                ticket=ticket_with_context,
                event_type='escalated',
                channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
                user_email=user_email,
                user_id=ticket.get('user_id', 'unknown')
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
            
        return escalation

    def _notify_all_channels(self, company_id: int, ticket: Dict, routing: Dict) -> Dict:
        results = {}
        try:
            results["email"] = self._notify_email(company_id, ticket, routing)
        except Exception as e:
            results["email"] = f"failed: {e}"
        
        try:
            results["slack"] = self._notify_slack(company_id, ticket, routing)
        except Exception as e:
            results["slack"] = f"failed: {e}"
            
        try:
            results["jira"] = self._notify_jira(company_id, ticket, routing)
        except Exception as e:
            results["jira"] = f"failed: {e}"
            
        return results

    def _notify_email(self, company_id: int, ticket: Dict, routing: Dict) -> str:
        from src.models import CompanySettings
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        
        cfg = CompanySettings.get_email_config(company_id)
        if not cfg.get('enabled') or not cfg.get('smtp_password'):
            return "skipped"
            
        agent = routing["recommended_agent"]
        priority = ticket.get("priority", "Medium")
        ticket_id = ticket.get("id", "UNKNOWN")
        to_addr = agent["email"]
        from_addr = cfg.get("from_email") or cfg.get("smtp_user", "")
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[ECE Recommendation] You've been suggested for {priority}-priority ticket {ticket_id}"
        msg["From"] = from_addr
        msg["To"] = to_addr
        
        text_body = f"Ticket {ticket_id}\nPriority: {priority}\nRecommended Agent: {agent['name']}"
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        
        with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg['smtp_user'], cfg['smtp_password'])
            server.sendmail(from_addr, [to_addr], msg.as_bytes())
            
        return "sent"

    def _notify_slack(self, company_id: int, ticket: Dict, routing: Dict) -> str:
        from src.models import CompanySettings
        cfg = CompanySettings.get_slack_config(company_id)
        if not cfg.get('enabled') or not cfg.get('bot_token'):
            return "skipped"
            
        try:
            from slack_sdk import WebClient
        except ImportError:
            return "failed: slack_sdk not installed"
            
        client = WebClient(token=cfg['bot_token'])
        channel = cfg.get('channels', {}).get('escalations', 'it-escalations')
        tid = ticket.get("id", "UNKNOWN")
        agent = routing["recommended_agent"]
        
        client.chat_postMessage(
            channel=f"#{channel}",
            text=f"Escalation recommendation for ticket {tid}: *{agent['name']}*",
        )
        return "sent"

    def _notify_jira(self, company_id: int, ticket: Dict, routing: Dict) -> str:
        from src.models import CompanySettings
        import json
        import urllib.request
        from base64 import b64encode
        
        cfg = CompanySettings.get_jira_config(company_id)
        if not cfg.get('enabled') or not cfg.get('api_token'):
            return "skipped"
            
        issue_key = ticket.get("jira_key") or ticket.get("id", "")
        if not issue_key or not cfg.get('base_url'):
            return "skipped"
            
        url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/issue/{issue_key}/comment"
        agent = routing["recommended_agent"]
        body_adf = {
            "version": 1,
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": f"ECE Recommends agent: {agent['name']} for escalation."}]
            }]
        }
        
        payload = json.dumps({"body": body_adf}).encode("utf-8")
        token = b64encode(f"{cfg['email']}:{cfg['api_token']}".encode()).decode()
        
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }, method="POST")
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            
        if 200 <= status < 300:
            return "sent"
        return f"failed: HTTP {status}"


class TicketStateMachine:
    """
    Manages ticket lifecycle and valid state transitions
    """
    
    # Define valid state transitions
    VALID_TRANSITIONS = {
        TicketStatus.CREATED: [TicketStatus.TRIAGED],
        TicketStatus.TRIAGED: [TicketStatus.PROCESSING, TicketStatus.COMPLETED],
        TicketStatus.PROCESSING: [
            TicketStatus.SOLUTION_PROPOSED,
            TicketStatus.ESCALATED,
            TicketStatus.FAILED
        ],
        TicketStatus.SOLUTION_PROPOSED: [
            TicketStatus.QUALITY_CHECK,
            TicketStatus.PROCESSING
        ],
        TicketStatus.QUALITY_CHECK: [
            TicketStatus.APPROVED,
            TicketStatus.PROCESSING
        ],
        TicketStatus.APPROVED: [TicketStatus.EXECUTING],
        TicketStatus.EXECUTING: [
            TicketStatus.COMPLETED,
            TicketStatus.AWAITING_USER,
            TicketStatus.FAILED
        ],
        TicketStatus.AWAITING_USER: [
            TicketStatus.EXECUTING,
            TicketStatus.COMPLETED
        ],
        TicketStatus.ESCALATED: [TicketStatus.PROCESSING, TicketStatus.COMPLETED],
        TicketStatus.COMPLETED: [],
        TicketStatus.FAILED: [TicketStatus.ESCALATED]
    }
    
    def __init__(self):
        """Initialize state machine"""
        self.transition_history = []
        logger.info("TicketStateMachine initialized")
    
    def transition(
        self,
        ticket: Dict,
        new_status: TicketStatus,
        reason: Optional[str] = None
    ) -> Dict:
        """
        Transition ticket to new status
        
        Args:
            ticket: Current ticket object
            new_status: Target TicketStatus
            reason: Optional reason for transition
            
        Returns:
            Updated ticket
            
        Raises:
            ValueError if transition is not allowed
        """
        current_status = TicketStatus(ticket.get('status', 'created'))
        
        # Validate transition
        if new_status not in self.VALID_TRANSITIONS[current_status]:
            raise ValueError(
                f"Invalid transition from {current_status.value} to {new_status.value}"
            )
        
        # Record transition
        transition_record = {
            'ticket_id': ticket['id'],
            'from_status': current_status.value,
            'to_status': new_status.value,
            'timestamp': datetime.now().isoformat(),
            'reason': reason
        }
        
        # Update ticket
        ticket['status'] = new_status.value
        ticket['updated_at'] = datetime.now().isoformat()
        
        # Log transition
        self.transition_history.append(transition_record)
        logger.info(f"Ticket {ticket['id']}: {current_status.value} → {new_status.value}")
        
        return ticket
    
    def get_ticket_history(self, ticket_id: str) -> List[Dict]:
        """Get transition history for a ticket"""
        return [
            t for t in self.transition_history
            if t['ticket_id'] == ticket_id
        ]


# ============================================================================
# MAIN AUTOMATION SPECIALIST CLASS
# ============================================================================

class AutomationSpecialist:
    """
    Main Automation Specialist coordinating all components
    """
    
    def __init__(self, email_config: Optional[Dict] = None):
        """
        Initialize Automation Specialist
        
        Args:
            email_config: Email configuration (uses config.EMAIL_CONFIG if None)
        """
        self.notification_manager = NotificationManager(email_config)
        self.execution_engine = ExecutionEngine()
        self.escalation_manager = EscalationManager(self.notification_manager)
        self.state_machine = TicketStateMachine()
        
        logger.info("AutomationSpecialist initialized successfully")

    def notify_ticket_resolution(self, ticket_data: Dict, result: Dict, user_email: str):
        """
        Notify user about the resolution (Solution or Escalation)
        This is called immediately after prediction.
        """
        try:
            if result.get('escalated'):
                # Escalation is handled inside escalation_manager.escalate_ticket
                # But we need to call it if it wasn't called yet?
                # In app.py currently:
                # 1. result = solver.solve()
                # 2. if escalated: automation_specialist.escalation_manager.escalate_ticket()
                # So we just wrap that logic or verify it's called.
                
                # If we use this method as the SOLE notification trigger in app.py:
                # We should call escalate_ticket here.
                
                reason = result.get('escalation_reason', 'Automated escalation')
                self.escalation_manager.escalate_ticket(
                    ticket=ticket_data,
                    reason=reason,
                    user_email=user_email
                )
            else:
                # Solution proposed
                # We manually trigger a 'solution_proposed' notification
                # And we include the solution text in the ticket data so template sees it
                
                ticket_with_sol = ticket_data.copy()
                ticket_with_sol['solution_text'] = result.get('solution', '')
                
                self.notification_manager.send_notification(
                    ticket=ticket_with_sol,
                    event_type='solution_proposed',
                    channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
                    user_email=user_email,
                    user_id=ticket_data.get('user_id', 'unknown')
                )
                logger.info(f"Resolution email sent to {user_email}")
                
        except Exception as e:
            logger.error(f"Failed to notify ticket resolution: {e}")
    
    def process_approved_solution(
        self,
        ticket: Dict,
        approved_solution: Dict,
        user_email: str
    ) -> Dict:
        """
        Main entry point: Process an approved solution from Quality Gatekeeper
        
        Args:
            ticket: Ticket object
            approved_solution: Solution approved by Quality Gatekeeper
            user_email: User's email for notifications
            
        Returns:
            Processing result with execution details
        """
        logger.info(f"Processing approved solution for ticket {ticket['id']}")
        
        try:
            # Transition to APPROVED status
            ticket = self.state_machine.transition(
                ticket,
                TicketStatus.APPROVED,
                reason='Quality Gatekeeper approved solution'
            )
            
            # Notify user
            self.notification_manager.send_notification(
                ticket=ticket,
                event_type='approved',
                channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
                user_email=user_email,
                user_id=ticket.get('user_id', 'unknown')
            )
            
            # Transition to EXECUTING
            ticket = self.state_machine.transition(
                ticket,
                TicketStatus.EXECUTING,
                reason='Starting solution execution'
            )
            
            # Notify execution started
            self.notification_manager.send_notification(
                ticket=ticket,
                event_type='executing',
                channels=[NotificationChannel.POPUP],
                user_email=user_email,
                user_id=ticket.get('user_id', 'unknown')
            )
            
            # Execute solution
            execution_result = self.execution_engine.execute_solution(
                ticket,
                approved_solution
            )
            
            # Handle result based on solution type
            if execution_result['solution_type'] == SolutionType.AUTOMATED.value:
                # Automated execution completed
                ticket = self.state_machine.transition(
                    ticket,
                    TicketStatus.COMPLETED,
                    reason='Automated solution executed successfully'
                )
                
                self.notification_manager.send_notification(
                    ticket=ticket,
                    event_type='completed',
                    channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
                    user_email=user_email,
                    user_id=ticket.get('user_id', 'unknown')
                )
                
            elif execution_result['solution_type'] == SolutionType.GUIDED.value:
                # Awaiting user to follow steps
                ticket = self.state_machine.transition(
                    ticket,
                    TicketStatus.AWAITING_USER,
                    reason='User needs to follow provided steps'
                )
                
                self.notification_manager.send_notification(
                    ticket=ticket,
                    event_type='awaiting_user',
                    channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
                    user_email=user_email,
                    user_id=ticket.get('user_id', 'unknown')
                )
                
            elif execution_result.get('requires_human'):
                # Escalate to human team
                escalation = self.escalation_manager.escalate_ticket(
                    ticket=ticket,
                    reason=execution_result.get('reason', 'Requires human expertise'),
                    user_email=user_email
                )
                
                ticket = self.state_machine.transition(
                    ticket,
                    TicketStatus.ESCALATED,
                    reason=f"Escalated to {escalation['department']} department"
                )
                
                execution_result['escalation'] = escalation
            
            return {
                'success': True,
                'ticket': ticket,
                'execution': execution_result
            }
            
        except Exception as e:
            logger.error(f"Failed to process solution for {ticket['id']}: {e}")
            
            # Escalate on failure
            escalation = self.escalation_manager.escalate_ticket(
                ticket=ticket,
                reason=f"Processing failed: {str(e)}",
                user_email=user_email
            )
            
            ticket = self.state_machine.transition(
                ticket,
                TicketStatus.ESCALATED,
                reason='Processing failed - escalated to human team'
            )
            
            return {
                'success': False,
                'ticket': ticket,
                'error': str(e),
                'escalation': escalation
            }
    
    def notify_user_on_login(self, user_id: str, user_email: str) -> List[Dict]:
        """
        Send pop-up notifications for all active tickets when user logs in
        
        Args:
            user_id: User's ID
            user_email: User's email
            
        Returns:
            List of notifications sent
        """
        logger.info(f"Sending login notifications for user {user_id}")
        
        # In real implementation, fetch active tickets from database
        # For now, return empty list
        notifications = []
        
        return notifications
    
    def mark_ticket_completed(
        self,
        ticket: Dict,
        user_email: str,
        user_feedback: Optional[Dict] = None
    ) -> Dict:
        """
        Mark ticket as completed (called when user confirms resolution)
        
        Args:
            ticket: Ticket object
            user_email: User's email
            user_feedback: Optional user feedback/rating
            
        Returns:
            Updated ticket
        """
        logger.info(f"Marking ticket {ticket['id']} as completed")
        
        ticket = self.state_machine.transition(
            ticket,
            TicketStatus.COMPLETED,
            reason='User confirmed resolution'
        )
        
        self.notification_manager.send_notification(
            ticket=ticket,
            event_type='completed',
            channels=[NotificationChannel.EMAIL],
            user_email=user_email,
            user_id=ticket.get('user_id', 'unknown')
        )
        
        if user_feedback:
            logger.info(f"User feedback received for {ticket['id']}: {user_feedback}")
        
        return ticket

    def process_inbound_email(self, email_subject: str, email_body: str) -> Dict[str, Any]:
        """
        Processes an inbound email reply, extracts the ticket ID, and detects intents
        like RESOLVED or NEEDS_MORE_HELP to sync ECE and Jira status.
        """
        import re
        from src.jira_integration import JiraIntegration, get_jira_key
        
        # 1. Extract Ticket ID from subject
        match = re.search(r'ECE-([A-Z0-9]+)', email_subject, re.IGNORECASE)
        if not match:
            # Fallback to the format used in our app (e.g. Ticket #CMP1230001)
            match = re.search(r'Ticket\s*#([A-Z0-9]+)', email_subject, re.IGNORECASE)
            
        if not match:
            logger.warning(f"Could not extract ticket ID from subject: '{email_subject}'")
            return {"success": False, "error": "No ticket ID in subject"}
            
        ticket_id = match.group(1)
        body_lower = email_body.lower()
        
        # 2. Detect intents
        resolved_keywords = ['resolved', 'fixed', 'working', 'thank you', 'thanks']
        needs_help_keywords = ['still', 'not working', 'no', 'need help', "didn't work", "does not work"]
        
        is_resolved = any(kw in body_lower for kw in resolved_keywords)
        needs_help = any(kw in body_lower for kw in needs_help_keywords)
        
        # Determine primary intent (mutually exclusive)
        if is_resolved and needs_help:
            # If both are present, prioritize needing help just to be safe
            is_resolved = False

        if is_resolved:
            # ── FLOW 3: mark resolved ───────────────────────────────────────────────
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                conn.execute(
                    """UPDATE classified_tickets
                       SET status = 'resolved', corrected = 1
                       WHERE id = ?""",
                    (ticket_id,)
                )
                conn.execute(
                    """INSERT INTO ticket_interactions
                       (ticket_id, sender, message, timestamp)
                       VALUES (?, 'system', ?, ?)""",
                    (
                        ticket_id,
                        "User confirmed resolution via email reply.",
                        datetime.now().isoformat(),
                    )
                )
                conn.commit()
                conn.close()
                logger.info(f"ECE ticket {ticket_id} marked resolved via email reply")
            except Exception as e:
                logger.error(f"Email-resolve ECE DB update failed: {e}")
            try:
                from app import audit_log
                audit_log('TICKET_RESOLVED', ticket_id, 'system', "Ticket marked resolved via email reply.")
            except Exception as e:
                logger.error(f"Audit log resolve failed: {e}")

            try:
                _jira = JiraIntegration()
                jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
                if jira_key:
                    _jira.update_issue_resolved(
                        jira_key   = jira_key,
                        solution   = "Resolved by user via email reply.",
                        ticket_id  = ticket_id,
                        confidence = 1.0,
                    )
                    logger.info(f"Jira {jira_key} marked Done via email resolution sync")
            except Exception as e:
                logger.error(f"Email-resolve Jira sync failed: {e}")
                
            return {"success": True, "intent": "RESOLVED", "ticket_id": ticket_id}

        elif needs_help:
            # ── FLOW 4: mark escalated ──────────────────────────────────────────────
            try:
                conn = sqlite3.connect(config.DATABASE_PATH)
                conn.execute(
                    """UPDATE classified_tickets
                       SET status = 'escalated'
                       WHERE id = ?""",
                    (ticket_id,)
                )
                conn.execute(
                    """INSERT INTO ticket_interactions
                       (ticket_id, sender, message, timestamp)
                       VALUES (?, 'system', ?, ?)""",
                    (
                        ticket_id,
                        "User indicated via email that the issue is not resolved. Escalating.",
                        datetime.now().isoformat(),
                    )
                )
                conn.commit()
                conn.close()
                logger.info(f"ECE ticket {ticket_id} escalated via email reply")
            except Exception as e:
                logger.error(f"Email-escalate ECE DB update failed: {e}")
            try:
                from app import audit_log
                audit_log('TICKET_ESCALATED', ticket_id, 'system', "Ticket escalated via email reply due to user needing more help.")
            except Exception as e:
                logger.error(f"Audit log escalate failed: {e}")

            try:
                _jira = JiraIntegration()
                jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
                if jira_key:
                    _jira.update_issue_escalated(
                        jira_key          = jira_key,
                        ticket_id         = ticket_id,
                        escalation_reason = "User replied via email: issue not resolved.",
                    )
                    logger.info(f"Jira {jira_key} moved to In Progress via email escalation sync")
            except Exception as e:
                logger.error(f"Email-escalate Jira sync failed: {e}")
                
            return {"success": True, "intent": "NEEDS_MORE_HELP", "ticket_id": ticket_id}

        else:
            # Just a normal reply, could log the interaction but that's outside the requested scope
            return {"success": True, "intent": "UNKNOWN", "ticket_id": ticket_id}


# ============================================================================
# TESTING / DEMO
# ============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("AUTOMATION SPECIALIST - Demo")
    print("=" * 70)
    
    # Create sample ticket
    sample_ticket = {
        'id': 'DEMO-001',
        'user_id': 'user123',
        'subject': 'Cannot access VPN',
        'body': 'VPN connection keeps dropping after 5 minutes',
        'type': 'incident',
        'priority': 'high',
        'status': 'approved'
    }
    
    # Create sample solution
    sample_solution = {
        'text': """1. Open VPN settings
2. Clear cached credentials
3. Reconnect to VPN
4. Test connection for 10 minutes""",
        'confidence': 0.85
    }
    
    # Initialize Automation Specialist
    automation_specialist = AutomationSpecialist()
    
    # Process solution
    result = automation_specialist.process_approved_solution(
        ticket=sample_ticket,
        approved_solution=sample_solution,
        user_email='demo@example.com'
    )
    
    print("\n" + "=" * 70)
    print("PROCESSING RESULT:")
    print("=" * 70)
    print(json.dumps(result, indent=2))
    
    print("\n" + "=" * 70)
    print("Demo completed successfully!")
    print("=" * 70)
