import threading
import logging
import sqlite3
from datetime import datetime
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk import WebClient
import config

logger = logging.getLogger(__name__)

def handle_action(client: SocketModeClient, req: SocketModeRequest):
    # Acknowledge immediately
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    payload = req.payload
    if 'actions' not in payload or not payload['actions']:
        return

    action_id = payload['actions'][0]['action_id']
    ticket_id = payload['actions'][0].get('value')
    user_id = payload['user']['id']

    if not ticket_id:
        return

    from app import jira_client, slack, audit_log
    from src.jira_integration import get_jira_key

    try:
        if action_id == "ticket_resolved":
            conn = sqlite3.connect(config.DATABASE_PATH)
            conn.execute("UPDATE classified_tickets SET status='resolved', corrected=1 WHERE id=?", (ticket_id,))
            conn.execute("INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'slack', 'User confirmed resolved via Slack button', ?)", (ticket_id, datetime.now().isoformat()))
            
            # Fetch subject for notification
            cur = conn.cursor()
            cur.execute("SELECT subject FROM classified_tickets WHERE id=?", (ticket_id,))
            row = cur.fetchone()
            subject = row[0] if row else "Unknown ticket"
            conn.commit()
            conn.close()

            jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
            if jira_key:
                jira_client.update_issue_resolved(
                    jira_key=jira_key,
                    solution="Resolved by user confirmation via Slack DM.",
                    ticket_id=ticket_id,
                    confidence=1.0
                )

            slack.notify_resolved(ticket_id, subject, user_slack_id=user_id, jira_key=jira_key)
            audit_log('TICKET_RESOLVED', ticket_id, 'slack', f"User confirmed resolved via Slack button by {user_id}")
            logger.info(f"Ticket {ticket_id} resolved via Slack button by {user_id}")

        elif action_id == "ticket_escalate":
            conn = sqlite3.connect(config.DATABASE_PATH)
            conn.execute("UPDATE classified_tickets SET status='escalated' WHERE id=?", (ticket_id,))
            conn.execute("INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'slack', 'User requested escalation via Slack button', ?)", (ticket_id, datetime.now().isoformat()))
            
            cur = conn.cursor()
            cur.execute("SELECT subject, pred_queue FROM classified_tickets WHERE id=?", (ticket_id,))
            row = cur.fetchone()
            subject = row[0] if row else "Unknown ticket"
            queue = row[1] if row else "IT Support"
            conn.commit()
            conn.close()

            jira_key = get_jira_key(config.DATABASE_PATH, ticket_id)
            if jira_key:
                jira_client.update_issue_escalated(
                    jira_key=jira_key,
                    ticket_id=ticket_id,
                    escalation_reason="User clicked Still need help in Slack."
                )

            slack.notify_escalation(ticket_id, subject, queue, 'User clicked Still need help in Slack', user_slack_id=user_id, jira_key=jira_key)
            audit_log('TICKET_ESCALATED', ticket_id, 'slack', f"User escalated via Slack button by {user_id}")
            logger.info(f"Ticket {ticket_id} escalated via Slack button by {user_id}")

        elif action_id == "claim_ticket":
            conn = sqlite3.connect(config.DATABASE_PATH)
            conn.execute("INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp) VALUES (?, 'slack', ?, ?)", (ticket_id, f'Ticket claimed by Slack user {user_id}', datetime.now().isoformat()))
            conn.commit()
            conn.close()

            # Ephemeral message to claimant
            channel_id = payload['container']['channel_id']
            try:
                slack.client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"You've claimed ticket {ticket_id}. The user has been notified."
                )
            except Exception as e:
                logger.error(f"Cannot post ephemeral Slack message: {e}")

            audit_log('TICKET_CLAIMED', ticket_id, 'slack', f"Ticket claimed by Slack user {user_id}")
            logger.info(f"Ticket {ticket_id} claimed by {user_id} via Slack")

        elif action_id == "create_incident":
            logger.info(f"Action '{action_id}' clicked by {user_id} for alert {ticket_id}")
            channel_id = payload['container']['channel_id']
            try:
                slack.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"Ack: Incident creation initiated for {ticket_id}")
            except Exception:
                pass

        elif action_id == "send_comms":
            logger.info(f"Action '{action_id}' clicked by {user_id} for alert {ticket_id}")
            channel_id = payload['container']['channel_id']
            try:
                slack.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"Ack: Comms broadcasting initiated for {ticket_id}")
            except Exception:
                pass

        else:
            logger.warning(f"Slack: unhandled action_id '{action_id}' — no handler matched")

    except Exception as e:
        logger.error(f"Error handling Slack action {action_id}: {e}")

def handle_message(client: SocketModeClient, req: SocketModeRequest):
    # Acknowledge immediately
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    event = req.payload.get('event', {})
    text = event.get('text', '').lower().strip()
    user_id = event.get('user', '')

    if not text or not user_id: return

    resolved_keywords = ['resolved', 'fixed', 'working', 'yes', 'thanks']
    escalated_keywords = ['no', 'still', "didn't", 'not working', 'help']

    is_resolved = any(k in text for k in resolved_keywords)
    is_escalated = any(k in text for k in escalated_keywords)

    if not is_resolved and not is_escalated:
        return

    try:
        conn = sqlite3.connect(config.DATABASE_PATH)
        cur = conn.cursor()
        
        # Look up most recent open ticket for this slack user
        # user_slack_id might be stored in classified_tickets
        cur.execute("SELECT id FROM classified_tickets WHERE user_slack_id=? AND status NOT IN ('resolved', 'escalated', 'closed') ORDER BY timestamp DESC LIMIT 1", (user_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            ticket_id = row[0]
            # Mock an interactive req payload to reuse our own function
            mock_req = SocketModeRequest(type="interactive", envelope_id=req.envelope_id, payload={
                "user": {"id": user_id},
                "actions": [{"action_id": "ticket_resolved" if is_resolved and not is_escalated else "ticket_escalate", "value": ticket_id}]
            })
            handle_action(client, mock_req)

    except Exception as e:
        logger.error(f"Error parsing DM reply for user {user_id}: {e}")


def handle_slash_command(client: SocketModeClient, req: SocketModeRequest):
    # Acknowledge immediately
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    payload = req.payload
    text = payload.get('text', '').strip().lower()
    channel_id = payload.get('channel_id', '')

    from app import slack

    if text in ('stats', ''):
        slack.post_stats(channel_id)
    else:
        try:
            slack.client.chat_postEphemeral(
                channel=channel_id,
                user=payload['user_id'],
                text="ECE Slack Commands:\n- `/ece stats`: Shows live ECE ticket stats"
            )
        except Exception:
            pass

def _dispatch(client: SocketModeClient, req: SocketModeRequest):
    if req.type == "interactive":
        handle_action(client, req)
    elif req.type == "events_api":
        handle_message(client, req)
    elif req.type == "slash_commands":
        handle_slash_command(client, req)

def start_socket_mode():
    if not config.SLACK['enabled'] or not config.SLACK['app_token']:
        logger.warning("Socket Mode disabled — SLACK_APP_TOKEN not set")
        return
    try:
        sm_client = SocketModeClient(
            app_token=config.SLACK['app_token'],
            web_client=WebClient(token=config.SLACK['bot_token']),
        )
        sm_client.socket_mode_request_listeners.append(_dispatch)
        sm_client.connect()
        logger.info("Slack Socket Mode listener started")
    except Exception as e:
        logger.error(f"Socket Mode failed to start: {e}")
