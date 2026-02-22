import sys
import os
import logging
from google_genai_email import GoogleGenAIEmailGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_email_generation():
    """Test the Google GenAI Email Generator."""
    print("Testing Google GenAI Email Generator...")
    
    # Check for API Key
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        print("⚠️ GOOGLE_API_KEY not found in environment variables.")
        print("Please set it to test actual generation.")
        # Proceeding to test initialization logic which handles missing key
    
    generator = GoogleGenAIEmailGenerator()
    
    if not generator.enabled:
        print("Generator is disabled (likely due to missing API key).")
        return
        
    print(f"Generator enabled with model: {generator.model_name}")
    
    # Mock ticket data
    ticket = {
        'id': 'TEST-1234',
        'subject': 'Unable to access VPN',
        'body': 'I am trying to connect to the VPN but I keep getting error 691. My username is jdoe.',
        'user_name': 'John Doe',
        'status': 'In Progress',
        'priority': 'High'
    }
    
    context = "We are currently investigating an outage with the VPN server in the US East region."
    
    print("\ngenerating email for ticket:")
    print(f"Subject: {ticket['subject']}")
    print(f"Body: {ticket['body']}")
    print(f"Context: {context}")
    
    try:
        email_content = generator.generate_email_content(ticket, context)
        print("\n--- GENERATED EMAIL CONTENT ---\n")
        print(email_content)
        print("\n-------------------------------\n")
        print("✅ Email generation test passed!")
    except Exception as e:
        print(f"❌ Email generation failed: {e}")

if __name__ == "__main__":
    test_email_generation()
