"""
SLACK SETUP — takes 5 minutes:
1. Go to https://api.slack.com/apps → "Create New App" → "From scratch"
2. Name: "ECE Assistant" · Workspace: your workspace
3. Go to "OAuth & Permissions" → add these Bot Token Scopes:
     chat:write        (send messages)
     im:write          (send DMs)
     im:read           (read DM info)
     im:history        (read DM replies — needed for Socket Mode)
     channels:read     (look up channels by name)
     commands          (slash commands)
4. Click "Install to Workspace" → copy the Bot Token (xoxb-...)
5. Go to "Basic Information" → copy the Signing Secret
6. Go to "Socket Mode" → Enable Socket Mode → generate App-Level Token
     (xapp-...) with scope: connections:write
7. Go to "Slash Commands" → Create command:
     Command: /ece
     Description: "ECE ticket stats and commands"
     Usage hint: "stats | help"
8. Go to "Event Subscriptions" → Enable Events (Socket Mode handles URL)
     Subscribe to bot events: message.im
9. Paste all tokens into .env
TEST JSON AT: https://app.slack.com/block-kit-builder
"""

import logging
import sqlite3
from datetime import datetime
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import config

logger = logging.getLogger(__name__)

class SlackIntegration:
    # ACTION ID REFERENCE — these strings must match exactly in
    # slack_events.py handle_action() and in every button below.
    # Changing one requires changing both.
    #   "ticket_resolved"  → user confirms resolution
    #   "ticket_escalate"  → user requests escalation
    #   "claim_ticket"     → engineer claims escalated ticket
    #   "create_incident"  → admin creates formal incident
    #   "send_comms"       → admin sends user comms for systemic alert

    def __init__(self):
        self.enabled = (
            config.SLACK['enabled']
            and bool(config.SLACK['bot_token'])
        )
        if self.enabled:
            self.client = WebClient(token=config.SLACK['bot_token'])
            self._channel_cache = {}
            logger.info("Slack integration enabled")
        else:
            self.client = None
            logger.warning(
                "Slack disabled — set SLACK_ENABLED=true and SLACK_BOT_TOKEN"
            )

    @staticmethod
    def _build_blocks(*elements) -> list:
        blocks = []
        for el in elements:
            if isinstance(el, str):
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": el}})
            elif isinstance(el, dict):
                blocks.append(el)
        return blocks

    def _get_channel_id(self, channel_name: str) -> Optional[str]:
        if not self.enabled: return None
        if channel_name in self._channel_cache:
            return self._channel_cache[channel_name]

        stripped_name = channel_name.lstrip("#")
        try:
            # We must paginate through conversations to find it reliably, or just get the first page for demo
            resp = self.client.conversations_list(types="public_channel,private_channel")
            for channel in resp.get("channels", []):
                if channel["name"] == stripped_name:
                    self._channel_cache[channel_name] = channel["id"]
                    return channel["id"]
            return None
        except SlackApiError as e:
            logger.error(f"Error fetching Slack channel ID for {channel_name}: {e}")
            return None

    def _get_user_dm_channel(self, slack_user_id: str) -> Optional[str]:
        if not self.enabled: return None
        try:
            resp = self.client.conversations_open(users=slack_user_id)
            return resp['channel']['id']
        except SlackApiError as e:
            logger.error(f"Error opening DM with {slack_user_id}: {e}")
            return None

    def _post_message(self, channel: str, blocks: list, text: str = "") -> bool:
        if not self.enabled: return False
        try:
            self.client.chat_postMessage(channel=channel, blocks=blocks, text=text)
            return True
        except SlackApiError as e:
            logger.error(f"Error posting Slack message to {channel}: {e}")
            return False

    # REQUIRED SLACK SCOPE: users:read.email
    # Go to api.slack.com/apps → your app → OAuth & Permissions
    # → Bot Token Scopes → Add: users:read.email
    # Then reinstall the app to the workspace.
    # Without this scope, this method always returns None.
    def _get_slack_user_id(self, email: str) -> Optional[str]:
        """
        Look up a Slack user ID (e.g. U012AB3CD) from an email address.
        Requires the users:read.email OAuth scope in Slack app settings.
        Returns None if not found or if Slack is disabled.

        NOTE: Only works if the user's ECE email matches their Slack
        workspace email. This is true in most enterprise environments.
        """
        if not self.enabled or not email:
            return None
        try:
            response = self.client.users_lookupByEmail(email=email)
            slack_id = response['user']['id']
            logger.debug(f"Slack user ID resolved for {email}: {slack_id}")
            return slack_id
        except SlackApiError as e:
            # Common error: account not found in workspace
            logger.warning(
                f"Slack user lookup failed for {email}: "
                f"{e.response.get('error', 'unknown_error')}"
            )
            return None
        except Exception as e:
            logger.error(f"Slack user lookup unexpected error: {e}")
            return None

    def notify_ticket_created(
        self,
        ticket_id: str,
        subject:   str,
        priority:  str,
        queue:     str,
        user_email: Optional[str] = None,
        user_slack_id: Optional[str] = None,
        jira_key:  Optional[str] = None,
    ) -> bool:
        if not self.enabled: return False

        # Auto-resolve Slack user ID from email if not already provided
        if not user_slack_id and user_email:
            user_slack_id = self._get_slack_user_id(user_email)

        priority_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "⚪")

        # 1. User DM
        if user_slack_id:
            dm_channel = self._get_user_dm_channel(user_slack_id)
            if dm_channel:
                blocks = self._build_blocks(
                    {"type": "header", "text": {"type": "plain_text", "text": f"🎫 Ticket Received — {ticket_id}"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"We've received your request:\n*{subject}*"}},
                    {"type": "section", "fields": [
                        {"type": "mrkdwn", "text": f"*Priority:* {priority_emoji} {priority}"},
                        {"type": "mrkdwn", "text": f"*Queue:* {queue}"},
                        {"type": "mrkdwn", "text": f"*Jira:* {jira_key or 'Creating...'}"}
                    ]},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": "Our AI is working on a solution — I'll update you here."}]}
                )
                self._post_message(dm_channel, blocks, text="Ticket Received")

        # 2. Team Channel
        channel_key = config.SLACK['queue_channel_map'].get(queue)
        channel_name = config.SLACK['channels'].get(channel_key, config.SLACK['channels']['logs'])
        channel_id = self._get_channel_id(channel_name)

        if channel_id:
            blocks = self._build_blocks(
                {"type": "header", "text": {"type": "plain_text", "text": f"🎫 New Ticket — {ticket_id}"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Priority:* {priority}"},
                    {"type": "mrkdwn", "text": f"*Queue:* {queue}"},
                    {"type": "mrkdwn", "text": f"*User:* <@{user_slack_id}>" if user_slack_id else "*User:* Unlinked Slack user"}
                ]},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Subject:* {subject}"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Jira: {jira_key or 'None'} · ECE AI processing"}]}
            )
            self._post_message(channel_id, blocks, text="New Ticket")
        return True

    def notify_solution_ready(
        self,
        ticket_id:     str,
        subject:       str,
        solution:      str,
        confidence:    float,
        user_email:    Optional[str] = None,
        user_slack_id: Optional[str] = None,
        jira_key:      Optional[str] = None,
    ) -> bool:
        if not self.enabled: return False
        
        if not user_slack_id and user_email:
            user_slack_id = self._get_slack_user_id(user_email)

        if len(solution) > 2800:
            solution = solution[:2800] + "... (truncated)"

        # 1. User DM
        if user_slack_id:
            dm_channel = self._get_user_dm_channel(user_slack_id)
            if dm_channel:
                blocks = self._build_blocks(
                    {"type": "header", "text": {"type": "plain_text", "text": f"✅ Solution Ready — {ticket_id}"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{subject}*"}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": solution}},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn", "text": "Did this resolve your issue?"}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ Yes, resolved"},
                                "style": "primary",
                                "action_id": "ticket_resolved",
                                "value": ticket_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "❌ Still need help"},
                                "style": "danger",
                                "action_id": "ticket_escalate",
                                "value": ticket_id
                            }
                        ]
                    },
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Confidence: {confidence:.0%} · Jira: {jira_key or 'None'}"}]}
                )
                self._post_message(dm_channel, blocks, text="Solution Ready")

        # 2. Team Channel Update (Send broadly to logs)
        channel_name = config.SLACK['channels']['logs']
        channel_id = self._get_channel_id(channel_name)
        if channel_id:
            blocks = self._build_blocks(
                {"type": "header", "text": {"type": "plain_text", "text": f"✅ AI Resolved — {ticket_id}"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": "*Method:* AI Auto"},
                    {"type": "mrkdwn", "text": f"*Confidence:* {confidence:.0%}"},
                    {"type": "mrkdwn", "text": f"*Jira:* {jira_key or 'None'}"}
                ]},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "Awaiting user confirmation via Slack DM"}]}
            )
            self._post_message(channel_id, blocks, text="AI Resolved")
            
        return True

    def notify_escalation(
        self,
        ticket_id:        str,
        subject:          str,
        queue:            str,
        escalation_reason: str,
        user_email:       Optional[str] = None,
        user_slack_id:    Optional[str] = None,
        jira_key:         Optional[str] = None,
        team_lead_slack_id: Optional[str] = None,
    ) -> bool:
        if not self.enabled: return False

        if not user_slack_id and user_email:
            user_slack_id = self._get_slack_user_id(user_email)

        # 1. User DM
        if user_slack_id:
            dm_channel = self._get_user_dm_channel(user_slack_id)
            if dm_channel:
                blocks = self._build_blocks(
                    {"type": "header", "text": {"type": "plain_text", "text": f"🚨 Your ticket has been escalated — {ticket_id}"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"Our AI wasn't able to fully resolve this one. A senior engineer from *{queue}* has been assigned and will reach out shortly.\n\n⏱ Expected response: *within 30 min*"}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Reference: Jira {jira_key or 'Pending'}"}]}
                )
                self._post_message(dm_channel, blocks, text="Ticket Escalated")

        # 2. Escalation Channel
        channel_name = config.SLACK['channels']['escalations']
        channel_id = self._get_channel_id(channel_name)
        if channel_id:
            ctx_text = "Please action within 30 min"
            if team_lead_slack_id:
                ctx_text = f"<@{team_lead_slack_id}> — {ctx_text}"

            blocks = self._build_blocks(
                {"type": "header", "text": {"type": "plain_text", "text": f"🚨 ESCALATION — {ticket_id}"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*User:* <@{user_slack_id}>" if user_slack_id else "*User:* Unknown Slack ID"},
                    {"type": "mrkdwn", "text": f"*AI Attempts:* 3/3 failed"},
                    {"type": "mrkdwn", "text": f"*Reason:* {escalation_reason}"}
                ]},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Subject:* {subject}"}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🙋 Claim Ticket"},
                            "style": "primary",
                            "action_id": "claim_ticket",
                            "value": ticket_id
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "💬 Message User"},
                            "action_id": "message_user",
                            "value": ticket_id
                        }
                    ]
                },
                {"type": "context", "elements": [{"type": "mrkdwn", "text": ctx_text}]}
            )

            if jira_key:
                # Append Jira button URL
                blocks[3]["elements"].append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📋 View in Jira"},
                    "url": f"{config.JIRA['base_url']}/browse/{jira_key}"
                })

            self._post_message(channel_id, blocks, text="Ticket Escalation Alert")
            
        return True

    def notify_systemic_alert(
        self,
        alert_id:   str,
        severity:   str,
        summary:    str,
        ticket_ids: list,
        jira_keys:  list,
        epic_key:   Optional[str] = None,
    ) -> bool:
        if not self.enabled: return False

        severity_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "WATCH": "🟡"}.get(severity, "⚪")
        channel_name = config.SLACK['channels']['incidents']
        channel_id = self._get_channel_id(channel_name)
        
        if channel_id:
            extra_tickets = ""
            if len(ticket_ids) > 8:
                extra_tickets = " + more"
            
            blocks = self._build_blocks(
                {"type": "header", "text": {"type": "plain_text", "text": f"{severity_emoji} SYSTEMIC ALERT — {severity} · {alert_id}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Alert ID:* {alert_id}"},
                    {"type": "mrkdwn", "text": f"*Severity:* {severity}"},
                    {"type": "mrkdwn", "text": f"*Tickets Clustered:* {len(ticket_ids)}"}
                ]},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Affected tickets:* {', '.join(ticket_ids[:8])}{extra_tickets}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Action required:* Assign a senior engineer and prepare user comms within 30 minutes."}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "⚡ Create Incident"},
                            "style": "primary",
                            "action_id": "create_incident",
                            "value": alert_id
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "📢 Send User Comms"},
                            "action_id": "send_comms",
                            "value": alert_id
                        }
                    ]
                },
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"ECE Pattern Miner · Detected at {datetime.now().strftime('%H:%M')}"}]}
            )

            if epic_key:
                blocks[2]["fields"].append({"type": "mrkdwn", "text": f"*Jira Epic:* {epic_key}"})
                blocks[5]["elements"].append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📋 View Epic"},
                    "url": f"{config.JIRA['base_url']}/browse/{epic_key}"
                })
            else:
                blocks[2]["fields"].append({"type": "mrkdwn", "text": f"*Jira Epic:* Creating..."})

            self._post_message(channel_id, blocks, text="Systemic Incident Alert")
            
        return True

    def notify_resolved(
        self,
        ticket_id:     str,
        subject:       str,
        user_slack_id: Optional[str] = None,
        jira_key:      Optional[str] = None,
    ) -> bool:
        if not self.enabled: return False

        if user_slack_id:
            dm_channel = self._get_user_dm_channel(user_slack_id)
            if dm_channel:
                blocks = self._build_blocks(
                    {"type": "header", "text": {"type": "plain_text", "text": f"🎉 All sorted — {ticket_id}"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"Great to hear! Your ticket *{subject}* is now marked as *Resolved*."}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Jira {jira_key or 'None'} updated · Have a great day!"}]}
                )
                self._post_message(dm_channel, blocks, text="Ticket Resolved Confirmation")
        return True

    def post_stats(self, channel_id: str) -> bool:
        if not self.enabled: return False
        try:
            conn = sqlite3.connect(config.DATABASE_PATH)
            cur = conn.cursor()
            
            cur.execute("SELECT COUNT(*) FROM classified_tickets")
            total = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'resolved'")
            resolved = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM classified_tickets WHERE status = 'escalated'")
            escalated = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM classified_tickets WHERE status NOT IN ('resolved','closed','escalated')")
            open_count = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM classified_tickets WHERE DATE(timestamp) = DATE('now')")
            today = cur.fetchone()[0] or 0
            
            conn.close()

            blocks = self._build_blocks(
                {"type": "header", "text": {"type": "plain_text", "text": "📊 ECE Live Stats"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Total:* {total}"},
                    {"type": "mrkdwn", "text": f"*Resolved:* {resolved}"},
                    {"type": "mrkdwn", "text": f"*Escalated:* {escalated}"},
                    {"type": "mrkdwn", "text": f"*Open:* {open_count}"},
                    {"type": "mrkdwn", "text": f"*Submitted Today:* {today}"}
                ]},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Updated: {datetime.now().strftime('%H:%M %d %b %Y')}"}]}
            )
            self._post_message(channel_id, blocks, text="Current ECE Stats")
            return True
        except Exception as e:
            logger.error(f"Error posting stats: {e}")
            return False
