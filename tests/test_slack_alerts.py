import os
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

from slack_integration import SlackIntegration

def run_test():
    slack = SlackIntegration()

    if not slack.enabled:
        print("❌ Slack is not enabled! Please check your .env file to ensure SLACK_ENABLED=true and tokens are set.")
        return

    # 👇 REPLACE THIS WITH YOUR REAL SLACK EMAIL 👇
    TEST_EMAIL = "eceproject2026@gmail.com" 

    print(f"Testing Slack Integration... (Using email: {TEST_EMAIL})")

    print("\n1. Testing Ticket Creation Notification...")
    success = slack.notify_ticket_created(
        ticket_id="TEST-1001",
        subject="I cannot connect to the VPN remotely",
        priority="High",
        queue="IT Support",
        user_email=TEST_EMAIL,
        jira_key="IT-404"
    )
    print(f"   Result: {'✅ Success' if success else '❌ Failed'}")

    print("\n2. Testing Solution Ready Notification...")
    success = slack.notify_solution_ready(
        ticket_id="TEST-1001",
        subject="I cannot connect to the VPN remotely",
        solution="Please restart your Cisco AnyConnect client and ensure your WiFi is stable. If you are abroad, try using the secondary gateway.",
        confidence=0.88,
        user_email=TEST_EMAIL,
        jira_key="IT-404"
    )
    print(f"   Result: {'✅ Success' if success else '❌ Failed'}")

    print("\n3. Testing Escalation Notification...")
    success = slack.notify_escalation(
        ticket_id="TEST-1002",
        subject="Need enterprise license for new design software",
        queue="IT Support",
        escalation_reason="AI cannot automatically provision paid enterprise licenses.",
        user_email=TEST_EMAIL,
        jira_key="IT-405"
    )
    print(f"   Result: {'✅ Success' if success else '❌ Failed'}")

    print("\nTest finished! Check your Slack workspace for the messages.")
    print("If you didn't receive the DM, double check that TEST_EMAIL matches your Slack workspace email exactly.")

if __name__ == "__main__":
    run_test()
