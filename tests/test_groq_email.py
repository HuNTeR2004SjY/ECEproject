
import os
import logging
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.groq_email_generator import GroqEmailGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_groq_email_generation():
    print("Testing Groq Email Generator...")
    
    # Check for API Key
    import config
    api_key = config.GROQ_API_KEY
    if not api_key:
        print("WARNING: GROQ_API_KEY not found in config or environment.")
        print("Test may fail or return disabled status.")
    
    generator = GroqEmailGenerator()
    
    if not generator.enabled:
        print("Generator is disabled (likely due to missing API key).")
        return

    ticket = {
        'id': '12345',
        'subject': 'Printer not working',
        'body': 'The HP LaserJet on the 2nd floor is jamming paper repeatedly.',
        'user_name': 'Sarah Connor',
        'status': 'Processing',
        'priority': 'High'
    }
    
    context = "We have dispatched a technician. ETA 2 hours."
    
    print("\n--- Generating Email ---")
    email_content = generator.generate_email_content(ticket, context)
    
    print("\n--- Generated Content ---")
    print(email_content)
    print("\n-------------------------")
    
    if "<p>" in email_content or "<html>" in email_content or "Sarah Connor" in email_content:
        print("SUCCESS: Email content generated successfully.")
    else:
        print("FAILURE: Generated content does not look right.")

if __name__ == "__main__":
    test_groq_email_generation()
