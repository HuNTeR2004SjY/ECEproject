"""
Status Reporter Module

This module implements the StatusReporter class, which acts as the downstream consumer
for the ProcessMonitor in the Enterprise Context Engine (ECE). It receives metric
snapshots, maintains a rolling history, checks for specific alert conditions (such as
SLA breaches, high escalation rates, database degradation, or agent unreachability),
and sends HTML-formatted alert emails to administrators when configured thresholds
are exceeded.
"""

import logging
import smtplib
import collections
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class StatusReporter:
    """
    Receives monitoring data and generates reports on process health and ticket
    updates for internal teams. Evaluates metrics against predefined thresholds
    and triggers email alerts.
    """

    def __init__(self, email_config: dict, admin_email: str):
        """
        Initializes the StatusReporter.

        Args:
            email_config (dict): Configuration dictionary for SMTP and email settings.
            admin_email (str): The destination email address for administrative alerts.
        """
        self.email_config = email_config
        self.admin_email = admin_email
        # Rolling window of the last 100 metric snapshots.
        # Note: We do not use a lock here because collections.deque append
        # operations are thread-safe in CPython due to the GIL. This allows
        # safe writes from the monitor thread and reads from API threads.
        self._snapshots = collections.deque(maxlen=100)
        # Tracks the last time an alert was sent to enforce a 10-minute cooldown
        self._last_alert_sent_at: Optional[datetime] = None
        self._alert_cooldown_minutes: int = 10
        
        logger.info(f"StatusReporter initialized. Admin: {admin_email}")

    def receive_metrics(self, metrics: dict) -> None:
        """
        Called by ProcessMonitor every check interval. Appends the metrics snapshot
        to the rolling history, evaluates alert conditions, and sends an email if
        alerts are triggered and the cooldown period has expired.

        Args:
            metrics (dict): A dictionary containing system health and performance metrics.
        """
        self._snapshots.append(metrics)
        alerts = self._check_alerts(metrics)

        if alerts:
            # Check if the 10-minute cooldown has expired (or if no alert has ever been sent)
            if self._last_alert_sent_at is None or \
               (datetime.now() - self._last_alert_sent_at) >= timedelta(minutes=self._alert_cooldown_minutes):
                
                email_sent = self._send_alert_email(alerts, metrics)
                # Always update cooldown to prevent hammering SMTP on every cycle if it fails
                self._last_alert_sent_at = datetime.now()
                
                if not email_sent:
                    logger.warning("Alerts triggered but email failed or was disabled. Cooldown started anyway.")
            else:
                logger.info("Alerts detected but suppressed due to cooldown period.")
        else:
            logger.info("Health check passed — no alerts.")

    def get_latest_report(self) -> dict:
        """
        Returns the most recent metrics snapshot.

        Returns:
            dict: The latest metrics dictionary, or an empty dict if none exist.
        """
        return self._snapshots[-1] if self._snapshots else {}

    def get_report_history(self, n: int = 10) -> list:
        """
        Returns the last n metrics snapshots, with the newest first.

        Args:
            n (int): The maximum number of historical snapshots to retrieve.

        Returns:
            list: A list of metrics dictionaries.
        """
        n = max(1, n)
        return list(reversed(list(self._snapshots)))[:n]

    def _check_alerts(self, metrics: dict) -> list:
        """
        Evaluates metrics against alert thresholds.
        
        Thresholds:
        1. SLA breach count > 0
        2. Escalation rate > 30.0%
        3. Any agent health status is "unreachable" or "down"
        4. DB status is "degraded"

        Args:
            metrics (dict): The current metrics snapshot.

        Returns:
            list: A list of alert message strings. Returns an empty list if no alerts.
        """
        alerts = []

        # 1. Check for SLA breaches
        if metrics.get("sla_breach_count", 0) > 0:
            breaches = metrics.get("sla_breaches_by_priority", {})
            alerts.append(
                f"{metrics.get('sla_breach_count', 0)} ticket(s) breached SLA "
                f"(High: {breaches.get('High', 0)}, "
                f"Medium: {breaches.get('Medium', 0)}, "
                f"Low: {breaches.get('Low', 0)})"
            )

        # 2. Check escalation rate
        esc_rate = metrics.get("escalation_rate_pct", 0.0)
        if esc_rate > 30.0:
            alerts.append(
                f"Escalation rate is {esc_rate}% — exceeds 30% threshold"
            )

        # 3. Check agent health
        for agent, status in metrics.get("agent_health", {}).items():
            if status in ("unreachable", "down"):
                alerts.append(f"Agent '{agent}' is {status}")

        # 4. Check DB status
        if metrics.get("db_status") == "degraded":
            alerts.append("Database status is degraded")

        return alerts

    def _build_alert_html(self, alerts: list, metrics: dict) -> str:
        """
        Builds and returns the HTML formatted string for the alert email.

        Args:
            alerts (list): The list of alert message strings.
            metrics (dict): The metrics snapshot to display in the email.

        Returns:
            str: The raw HTML content for the email body.
        """
        # Create alert bullet points
        alerts_html = "".join([f"<li>{alert}</li>" for alert in alerts])
        
        # Build agent health rows with emojis
        agent_rows_html = ""
        row_idx = 7 # Starting after the 6 static rows
        for agent, status in metrics.get("agent_health", {}).items():
            if status == "healthy":
                emoji = "✅"
            elif status in ("unreachable", "down"):
                emoji = "🔴"
            else:
                emoji = "⚠️"
            
            bg_color = "#ffffff" if row_idx % 2 != 0 else "#f9f9f9"
            agent_rows_html += f'''
                        <tr style="background-color: {bg_color};">
                            <td style="padding: 8px; border: 1px solid #ddd;">Agent {agent.capitalize()} Health</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{emoji} {status}</td>
                        </tr>'''
            row_idx += 1

        # Build the complete inline-styled HTML
        html_body = f'''
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <h2 style="color: #d9534f; border-bottom: 2px solid #d9534f; padding-bottom: 10px;">
                    ⚠️ ECE Health Alert
                </h2>
                <p><strong>Timestamp:</strong> {metrics.get('timestamp', datetime.now().isoformat())}</p>
                
                <div style="border: 2px solid #d9534f; background-color: #fdf2f2; padding: 15px; margin-bottom: 20px;">
                    <h3 style="color: #d9534f; margin-top: 0;">Alerts Triggered:</h3>
                    <ul>
                        {alerts_html}
                    </ul>
                </div>

                <h3>Metrics Snapshot</h3>
                <table style="width: 100%; max-width: 600px; border-collapse: collapse; margin-bottom: 20px;">
                    <thead>
                        <tr style="background-color: #f5f5f5;">
                            <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Metric</th>
                            <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Value</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr style="background-color: #ffffff;">
                            <td style="padding: 8px; border: 1px solid #ddd;">Total Tickets</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('total_tickets', 0)}</td>
                        </tr>
                        <tr style="background-color: #f9f9f9;">
                            <td style="padding: 8px; border: 1px solid #ddd;">Resolved</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('resolved_count', 0)}</td>
                        </tr>
                        <tr style="background-color: #ffffff;">
                            <td style="padding: 8px; border: 1px solid #ddd;">Escalated</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('escalation_count', 0)}</td>
                        </tr>
                        <tr style="background-color: #f9f9f9;">
                            <td style="padding: 8px; border: 1px solid #ddd;">Escalation Rate (%)</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('escalation_rate_pct', 0.0)}%</td>
                        </tr>
                        <tr style="background-color: #ffffff;">
                            <td style="padding: 8px; border: 1px solid #ddd;">SLA Breaches</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('sla_breach_count', 0)}</td>
                        </tr>
                        <tr style="background-color: #f9f9f9;">
                            <td style="padding: 8px; border: 1px solid #ddd;">Avg Resolution Time (mins)</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{metrics.get('avg_resolution_time_minutes', 0.0)}</td>
                        </tr>
                        {agent_rows_html}
                    </tbody>
                </table>
                
                <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 0.9em; color: #777;">
                    <em>This is an automated alert from the ECE Process Monitor.</em>
                </p>
            </body>
        </html>
        '''
        return html_body

    def _send_alert_email(self, alerts: list, metrics: dict) -> bool:
        """
        Generates and sends an HTML email via SMTP containing the alerts and the
        current metrics snapshot.
        
        Args:
            alerts (list): A list of alert messages to include in the email.
            metrics (dict): The current metrics snapshot.

        Returns:
            bool: True if the email was sent successfully, False otherwise.
        """
        if not self.email_config.get('enabled', False):
            logger.warning("Email disabled — skipping alert email")
            return False
            
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"⚠️ ECE System Alert — {len(alerts)} Issue(s) Detected"
            msg['From'] = self.email_config['from_email']
            msg['To'] = self.admin_email

            html_body = self._build_alert_html(alerts, metrics)
            msg.attach(MIMEText(html_body, 'html'))

            with smtplib.SMTP(self.email_config['smtp_host'],
                              self.email_config['smtp_port']) as server:
                server.starttls()
                server.login(self.email_config['smtp_user'],
                             self.email_config['smtp_password'])
                server.sendmail(self.email_config['from_email'],
                                self.admin_email,
                                msg.as_string())

            logger.info(f"Alert email sent to {self.admin_email}: {alerts}")
            return True

        except Exception as e:
            logger.error(f"Failed to send alert email: {e}", exc_info=True)
            return False
