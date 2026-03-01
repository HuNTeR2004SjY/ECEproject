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
                        logger.warning(f"Gmail API failed for {user_email}, no SMTP fallback attempted")
                threading.Thread(target=_api_send, daemon=True).start()
                return {'success': True, 'sent_at': datetime.now().isoformat(), 'note': 'Sending via Gmail API'}

            # --- Attempt 2 & 3: SMTP fallback (background thread) ---
            smtp_host = self.email_config['smtp_host']
            smtp_user = self.email_config['smtp_user']
            smtp_pass = self.email_config['smtp_password']
            TIMEOUT = 10

            def _smtp_send():
                # Try SSL:465
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

            threading.Thread(target=_smtp_send, daemon=True).start()
            return {'success': True, 'sent_at': datetime.now().isoformat(), 'note': 'Sending via SMTP in background'}

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
                        <a href="{config.APP_URL}/ticket/{ticket['id']}" class="action-button">
                            View Ticket Details
                        </a>
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
    Handles human escalations with intelligent routing
    """
    
    def __init__(self, notification_manager: NotificationManager):
        """Initialize escalation manager"""
        self.notification_manager = notification_manager
        self.escalation_history = []
        logger.info("EscalationManager initialized")
    
    def _get_department_email(self, department_name: str, ticket: Dict) -> str:
        """
        Fetch department email dynamically from DB, falling back to static config.
        """
        # Default fallback
        static_email = DEPARTMENT_ROUTING.get(department_name, {}).get('email', config.SUPPORT_EMAIL)
        
        try:
            user_id = ticket.get('user_id')
            if not user_id:
                return static_email
                
            conn = sqlite3.connect(config.DATABASE_PATH)
            cursor = conn.cursor()
            
            # 1. Get company_id from user
            # user_id in ticket might be string or int in DB? 
            # In app.py it saves user_id (str) into classified_tickets 'user_id' column if authenticated.
            # But wait, classified_tickets user_id might be "unknown" if not authenticated?
            # If so, we can't really do company specific routing easily without company info.
            # Assuming authenticated users for now.
            
            cursor.execute("SELECT company_id FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return static_email
                
            company_id = row[0]
            
            # 2. Get department email for this company
            cursor.execute(
                "SELECT email FROM departments WHERE company_id = ? AND name = ?", 
                (company_id, department_name)
            )
            dept_row = cursor.fetchone()
            conn.close()
            
            if dept_row:
                return dept_row[0]
            
        except Exception as e:
            logger.error(f"Error fetching dynamic department email: {e}")
            
        return static_email

    def escalate_ticket(
        self,
        ticket: Dict,
        reason: str,
        user_email: str
    ) -> Dict:
        """
        Escalate ticket to appropriate human team
        
        Args:
            ticket: Ticket object
            reason: Reason for escalation
            user_email: User's email for notifications
            
        Returns:
            Escalation record
        """
        logger.info(f"Escalating ticket {ticket['id']}: {reason}")
        
        # Determine correct department
        department = self._route_to_department(ticket)
        # Use simple get for static config just for SLA/keywords, but EMAIL comes from DB
        dept_config = DEPARTMENT_ROUTING.get(department, DEPARTMENT_ROUTING['IT'])
        
        # Dynamic Email Lookup
        dept_email = self._get_department_email(department, ticket)
        
        # Calculate SLA deadline
        sla_minutes = dept_config.get('escalation_sla', 60)
        sla_deadline = datetime.now() + timedelta(minutes=sla_minutes)
        
        # Create escalation record
        escalation = {
            'ticket_id': ticket['id'],
            'department': department,
            'department_email': dept_email,
            'reason': reason,
            'escalated_at': datetime.now().isoformat(),
            'escalated_by': 'automation_specialist',
            'priority': self._calculate_escalation_priority(ticket, reason),
            'sla_deadline': sla_deadline.isoformat(),
            'sla_minutes': sla_minutes
        }
        
        # Notify department
        self._notify_department(department, escalation, ticket, dept_email)
        
        # Notify user
        self._notify_user_escalation(ticket, department, user_email, escalation_data=escalation)
        
        # Log escalation
        self.escalation_history.append(escalation)
        
        return escalation
    
    def _route_to_department(self, ticket: Dict) -> str:
        """Determine which department should handle the ticket"""
        ticket_type = ticket.get('type', '').lower()
        ticket_text = f"{ticket.get('subject', '')} {ticket.get('body', '')}".lower()
        
        # Score each department
        scores = {}
        for dept, config in DEPARTMENT_ROUTING.items():
            score = 0
            
            # Type match
            if ticket_type in config['types']:
                score += 2
            
            # Keyword matches
            keyword_matches = sum(
                1 for keyword in config['keywords']
                if keyword in ticket_text
            )
            score += keyword_matches
            
            scores[dept] = score
        
        # Return department with highest score
        best_dept = max(scores, key=scores.get)
        
        # Fallback to IT if no clear match
        if scores[best_dept] == 0:
            return 'IT'
        
        return best_dept
    
    def _calculate_escalation_priority(self, ticket: Dict, reason: str) -> str:
        """Calculate escalation priority"""
        ticket_priority = ticket.get('priority', 'normal').lower()
        
        # If already high priority or multiple failures, escalate as urgent
        if ticket_priority in ['high', 'critical'] or 'failed' in reason.lower():
            return 'urgent'
        elif 'timeout' in reason.lower() or 'complex' in reason.lower():
            return 'high'
        else:
            return 'normal'
    
    def _notify_department(self, department: str, escalation: Dict, ticket: Dict, email_override: Optional[str] = None):
        """Send escalation email to department"""
        # Use override if provided, else fallback to static config
        dept_email = email_override
        if not dept_email:
            dept_email = DEPARTMENT_ROUTING.get(department, {}).get('email', config.SUPPORT_EMAIL)
        
        # Create escalation notification
        notification = {
            'title': f"🚨 Ticket Escalation: #{ticket['id']}",
            'message': f"Priority {escalation['priority']} ticket requires attention. Reason: {escalation['reason']}",
            'priority': escalation['priority'],
            'ticket_id': ticket['id'],
            'timestamp': datetime.now().isoformat()
        }
        
        # Generate detailed email content using Groq if available
        email_body_html = ""
        try:
             if self.notification_manager.ai_email_generator.enabled:
                # Prompt for Groq: Write an escalation email
                context = f"Reason for escalation: {escalation['reason']}. This ticket has been escalated to {department}."
                # We reuse the existing generator but might need a specific prompt method or just use the generic one
                # Let's use the generic one with a strong context
                ai_content = self.notification_manager.ai_email_generator.generate_email_content(
                    ticket, 
                    context=context
                )
                email_body_html = ai_content
        except Exception as e:
            logger.error(f"Groq generation failed for escalation: {e}")
            
        
        # Fallback HTML if Groq fails or is disabled
        if not email_body_html:
            email_body_html = f"""
            <div class="details">
                <p><strong>Ticket ID:</strong> {ticket['id']}</p>
                <p><strong>Priority:</strong> {escalation['priority']}</p>
                <p><strong>Reason:</strong> {escalation['reason']}</p>
                <p><strong>SLA Deadline:</strong> {escalation['sla_deadline']}</p>
            </div>
            
            <h3>Ticket Details:</h3>
            <p><strong>Subject:</strong> {ticket.get('subject', 'N/A')}</p>
            <p><strong>Description:</strong> {ticket.get('body', 'N/A')}</p>
            <p><strong>Type:</strong> {ticket.get('type', 'N/A')}</p>
            """
            
        # Final HTML wrapper
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .urgent {{ color: #dc2626; font-weight: bold; }}
                .details {{ background: #f3f4f6; padding: 15px; margin: 15px 0; }}
            </style>
        </head>
        <body>
            <h2 class="urgent">🚨 Ticket Escalation Required</h2>
            {email_body_html}
            <p><a href="{config.APP_URL}/ticket/{ticket['id']}">View Full Details →</a></p>
        </body>
        </html>
        """
        
        # Send email
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = notification['title']
            msg['From'] = self.notification_manager.email_config.get('from_email', 'noreply@ece-system.com')
            msg['To'] = dept_email
            msg.attach(MIMEText(html_content, 'html'))
            
            # Use notification manager's send method logic (checking enabled flag)
            if self.notification_manager.email_config.get('enabled', False):
                with smtplib.SMTP(
                    self.notification_manager.email_config['smtp_host'],
                    self.notification_manager.email_config['smtp_port']
                ) as server:
                    server.starttls()
                    server.login(
                        self.notification_manager.email_config['smtp_user'],
                        self.notification_manager.email_config['smtp_password']
                    )
                    server.send_message(msg)
                logger.info(f"Escalation email sent to {department} ({dept_email})")
            else:
                logger.info(f"Email disabled - would send escalation to {department} ({dept_email})")
            
        except Exception as e:
            logger.error(f"Failed to send escalation email: {e}")
    
    def _notify_user_escalation(self, ticket: Dict, department: str, user_email: str, escalation_data: Optional[Dict] = None):
        """Notify user that ticket has been escalated"""
        
        # Inject escalation details into notification context
        # We need a way to pass 'department' and 'sla' to _create_notification
        # Since send_notification takes ticket, we can temporarily augment it or 
        # reliance on the message is too simple. 
        # Best approach: NotificationManager._create_notification is rigid.
        # Let's override the notification object construction inside send_notification? NO.
        # Let's pass a modified ticket dict or rely on the Fact that NotificationManager is ours.
        
        # We will subclass or just hack the `ticket` dict passed to send_notification 
        # to include 'escalation_context' which we use in HTML generation.
        
        # ACTUALLY, we modified _generate_email_html to look at notification dict.
        # We need to ensure 'department' gets into notification dict.
        # _create_notification is the gatekeeper.
        # Let's modify send_notification to accept **kwargs? No, signature change = risky.
        
        # Workaround: Pass it in the 'ticket' object which flows through to _generate_email_html
        # WE will rely on _create_notification mostly copying ID/Subject.
        # _generate_email_html takes (notification, ticket).
        # So if we put data in ticket, we can read it.
        
        sla = 60
        if escalation_data:
            sla = escalation_data.get('sla_minutes', 60)
            
        ticket_with_context = ticket.copy()
        # We'll use these keys in our new HTML template logic
        # But wait, our HTML logic above used `notification.get()`. 
        # We should fix HTML logic to look at `ticket` OR
        # Update `send_notification` to allow custom data.
        
        # Let's just update `NotificationManager` to extract these if present in ticket?
        # Or better: Just put them in ticket_with_context and update HTML generation to read from ticket.
        
        # Correction on HTML replacement above: 
        # I used `notification.get('department')`. This means I need it in notification.
        # `_create_notification` returns the dict. 
        # I should manually construct the notification payload for maximum control here?
        # Or just update `_create_notification` to pull from ticket?
        
        # Let's assume for this specific requirement, we want to be explicit.
        # We will manually call send_notification but we need to ensure the data is there.
        
        # Let's monkey-patch the notification dictionary inside send_notification? No.
        
        # HACK: We will pass the data as part of the ticket object, and update _create_notification
        # to look for it.
        
        # Update: I will modify _create_notification in a separate chunk to copy these fields.
        
        ticket_with_context['escalated_to_dept'] = department
        ticket_with_context['escalated_sla'] = sla
        
        self.notification_manager.send_notification(
            ticket=ticket_with_context,
            event_type='escalated',
            channels=[NotificationChannel.EMAIL, NotificationChannel.POPUP],
            user_email=user_email,
            user_id=ticket.get('user_id', 'unknown')
        )


# ============================================================================
# STATUS TRACKER
# ============================================================================

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
