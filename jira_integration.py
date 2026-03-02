import os
import sqlite3
import requests
import logging
from typing import Optional, List
from requests.auth import HTTPBasicAuth

try:
    import config
except ImportError:
    pass  # Allow testing outside normal module flow if needed

logger = logging.getLogger(__name__)

class JiraIntegration:
    """
    Integrates ECE with Jira REST API to automatically create,
    update, and link Jira issues based on ECE ticket lifecycle.
    """
    
    def __init__(self, jira_config: dict = None):
        if jira_config is None:
            if hasattr(config, 'JIRA'):
                jira_config = config.JIRA
            else:
                jira_config = {
                    'base_url': os.getenv('JIRA_BASE_URL', 'https://yourcompany.atlassian.net'),
                    'email': os.getenv('JIRA_EMAIL', 'your@email.com'),
                    'api_token': os.getenv('JIRA_API_TOKEN', ''),
                    'project_key': os.getenv('JIRA_PROJECT_KEY', 'IT'),
                    'enabled': os.getenv('JIRA_ENABLED', 'true').lower() == 'true',
                }
                
        self.email = jira_config.get('email', '')
        self.api_token = jira_config.get('api_token', '')
        self.project_key = jira_config.get('project_key', 'IT')
        self.base_url = jira_config.get('base_url', '').rstrip('/')
        
        self.auth = HTTPBasicAuth(self.email, self.api_token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        self.enabled = jira_config.get('enabled', True)
        
        if self.enabled and not self.api_token:
            logger.warning("Jira enabled but no API token provided. Disabling Jira integration.")
            self.enabled = False

    def create_issue(
        self,
        ticket_id: str,
        subject: str,
        body: str,
        triage: dict,
        explanation: dict = None,
    ) -> Optional[str]:
        """Creates a Jira issue from an ECE ticket."""
        if not self.enabled:
            return None
            
        try:
            # Map ECE values to Jira values
            type_map = getattr(config, 'JIRA_TYPE_MAP', {})
            issue_type = type_map.get(triage.get('type'), 'Task')
            
            prio_map = getattr(config, 'JIRA_PRIORITY_MAP', {})
            jira_priority = prio_map.get(triage.get('priority'), 'Medium')
            
            queue_label = triage.get('queue', 'general').replace(' ', '-').lower()

            description = (
                f"ECE Ticket: {ticket_id}\n\n"
                f"Description:\n{body}\n\n"
                f"--- AI Triage ---\n"
                f"Type: {triage.get('type')} ({triage.get('type_confidence',0)*100:.0f}% confidence)\n"
                f"Priority: {triage.get('priority')} ({triage.get('priority_confidence',0)*100:.0f}%)\n"
                f"Queue: {triage.get('queue')} ({triage.get('queue_confidence',0)*100:.0f}%)\n"
            )
            
            if explanation:
                description += (
                    f"\n--- XAI Rationale ---\n"
                    f"Type reason: {explanation.get('type_reason','')}\n"
                    f"Priority reason: {explanation.get('priority_reason','')}\n"
                    f"Queue reason: {explanation.get('queue_reason','')}\n"
                    f"Evidence: {', '.join(explanation.get('evidence_signals',[]))}\n"
                )
                if explanation.get('needs_human_review'):
                    description += f"\n⚠ HUMAN REVIEW RECOMMENDED: {explanation.get('review_reason','')}\n"

            payload = {
                "fields": {
                    "project": {"key": self.project_key},
                    "summary": f"[ECE-{ticket_id}] {subject}",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}]
                        }]
                    },
                    "issuetype": {"name": issue_type},
                    "priority": {"name": jira_priority},
                    "labels": ["ece-auto", queue_label]
                }
            }
            
            url = f"{self.base_url}/rest/api/3/issue"
            response = requests.post(
                url, 
                json=payload, 
                auth=self.auth, 
                headers=self.headers,
                timeout=5
            )
            
            if response.status_code == 201:
                key = response.json().get('key')
                logger.info(f"Jira issue created: {key}")
                return key
            else:
                logger.error(f"Failed to create Jira issue. HTTP {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating Jira issue: {e}")
            return None

    def update_issue_resolved(
        self,
        jira_key: str,
        solution: str,
        ticket_id: str,
        confidence: float,
    ) -> bool:
        """Adds resolution comment and transitions Jira issue to Done."""
        if not self.enabled or not jira_key:
            return False
            
        try:
            # 1. Add comment
            comment_text = (
                f"✅ Resolved by ECE AI\n\n"
                f"Solution:\n{solution}\n\n"
                f"Confidence: {confidence*100:.0f}%\n"
                f"ECE Ticket: {ticket_id}"
            )
            
            comment_payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment_text}]
                    }]
                }
            }
            
            comment_url = f"{self.base_url}/rest/api/3/issue/{jira_key}/comment"
            requests.post(
                comment_url, 
                json=comment_payload, 
                auth=self.auth, 
                headers=self.headers,
                timeout=5
            )
            
            # 2. Get transition ID for "Done" or "Resolve Issue"
            trans_url = f"{self.base_url}/rest/api/3/issue/{jira_key}/transitions"
            resp = requests.get(trans_url, auth=self.auth, headers=self.headers, timeout=5)
            
            if resp.status_code == 200:
                transitions = resp.json().get('transitions', [])
                target_id = None
                
                for t in transitions:
                    name = t.get('name', '').lower()
                    if name in ['done', 'resolve issue', 'resolved', 'closed']:
                        target_id = t.get('id')
                        break
                        
                if target_id:
                    # 3. Apply transition
                    trans_payload = {"transition": {"id": target_id}}
                    requests.post(
                        trans_url, 
                        json=trans_payload, 
                        auth=self.auth, 
                        headers=self.headers,
                        timeout=5
                    )
            return True
            
        except Exception as e:
            logger.error(f"Error updating Jira resolved state for {jira_key}: {e}")
            return False

    def update_issue_escalated(
        self,
        jira_key: str,
        ticket_id: str,
        escalation_reason: str,
    ) -> bool:
        """Adds escalation comment and transitions Jira issue to In Progress."""
        if not self.enabled or not jira_key:
            return False
            
        try:
            comment_text = (
                f"🚨 Escalated to Human Team\n\n"
                f"ECE AI failed after 3 attempts.\nReason: {escalation_reason}\n"
                f"ECE Ticket: {ticket_id}\nAction required: Manual investigation."
            )
            
            comment_payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment_text}]
                    }]
                }
            }
            
            comment_url = f"{self.base_url}/rest/api/3/issue/{jira_key}/comment"
            requests.post(
                comment_url, 
                json=comment_payload, 
                auth=self.auth, 
                headers=self.headers,
                timeout=5
            )
            
            trans_url = f"{self.base_url}/rest/api/3/issue/{jira_key}/transitions"
            resp = requests.get(trans_url, auth=self.auth, headers=self.headers, timeout=5)
            
            if resp.status_code == 200:
                transitions = resp.json().get('transitions', [])
                target_id = None
                
                for t in transitions:
                    name = t.get('name', '').lower()
                    if name in ['in progress', 'start progress', 'open', 'to do']:
                        target_id = t.get('id')
                        break
                        
                if target_id:
                    trans_payload = {"transition": {"id": target_id}}
                    requests.post(
                        trans_url, 
                        json=trans_payload, 
                        auth=self.auth, 
                        headers=self.headers,
                        timeout=5
                    )
            return True
            
        except Exception as e:
            logger.error(f"Error updating Jira escalated state for {jira_key}: {e}")
            return False

    def create_systemic_epic(
        self,
        alert_id: str,
        severity: str,
        summary: str,
        ticket_ids: list,
        jira_keys: list,
    ) -> Optional[str]:
        """Creates an Epic for a systemic issue and links related Jira keys."""
        if not self.enabled:
            return None
            
        try:
            description = (
                f"ECE Pattern Miner detected a systemic issue.\n\n"
                f"Alert: {alert_id} | Severity: {severity}\n"
                f"Summary: {summary}\n\n"
                f"Affected ECE Tickets: {', '.join(ticket_ids)}\n"
                f"Linked Jira Issues: {', '.join(jira_keys)}"
            )
            
            payload = {
                "fields": {
                    "project": {"key": self.project_key},
                    "summary": f"[ECE SYSTEMIC {severity}] {alert_id}: VPN/Network Cluster",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}]
                        }]
                    },
                    "issuetype": {"name": "Epic"},
                    "priority": {"name": "High" if severity in ["CRITICAL", "HIGH"] else "Medium"},
                    "labels": ["ece-systemic", severity.lower()]
                }
            }
            
            url = f"{self.base_url}/rest/api/3/issue"
            resp = requests.post(url, json=payload, auth=self.auth, headers=self.headers, timeout=5)
            
            if resp.status_code != 201:
                logger.error(f"Failed to create Epic. HTTP {resp.status_code}: {resp.text}")
                return None
                
            epic_key = resp.json().get('key')
            logger.info(f"Systemic Epic created: {epic_key}")
            
            # Link individual Jira issues to this new Epic
            link_url = f"{self.base_url}/rest/api/3/issueLink"
            for key in jira_keys:
                if not key: continue
                link_payload = {
                    "type": {"name": "Relates"},
                    "inwardIssue": {"key": key},
                    "outwardIssue": {"key": epic_key}
                }
                requests.post(
                    link_url, 
                    json=link_payload, 
                    auth=self.auth, 
                    headers=self.headers, 
                    timeout=5
                )
                
            return epic_key
            
        except Exception as e:
            logger.error(f"Error creating systemic epic: {e}")
            return None

# Database helper functions
def save_jira_key(db_path: str, ticket_id: str, jira_key: str):
    """Saves the ECE Ticket ID to Jira Key mapping."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute('INSERT OR REPLACE INTO jira_keys (ticket_id, jira_key, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)', 
                    (ticket_id, jira_key))
        conn.commit()
    except Exception as e:
        logger.error(f"DB Error saving jira key: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def get_jira_key(db_path: str, ticket_id: str) -> Optional[str]:
    """Retrieves the Jira Key for an ECE ticket."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute('SELECT jira_key FROM jira_keys WHERE ticket_id = ?', (ticket_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"DB Error getting jira key: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
