"""
Google GenAI Email Generator
============================

Generates professional email content using Google's Gemini models.
"""

import logging
import google.generativeai as genai
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)

class GoogleGenAIEmailGenerator:
    """
    Generates email content using Google's Generative AI.
    """
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = 'gemini-pro'):
        """
        Initialize the generator.
        
        Args:
            api_key: Google API Key (optional, defaults to config)
            model_name: Model to use (default: gemini-pro)
        """
        self.api_key = api_key or config.GOOGLE_API_KEY
        self.model_name = model_name or config.GENAI_EMAIL_MODEL
        
        if not self.api_key:
            logger.warning("GOOGLE_API_KEY not found. Email generation will be disabled.")
            self.enabled = False
        else:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel(self.model_name)
                self.enabled = True
                logger.info(f"Google GenAI Email Generator initialized with model {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize Google GenAI: {e}")
                self.enabled = False

    def generate_email_content(self, ticket: Dict, context: str = "") -> str:
        """
        Generate email body based on ticket details.
        
        Args:
            ticket: Ticket dictionary
            context: Additional context (optional)
            
        Returns:
            Generated email content (HTML)
        """
        if not self.enabled:
            return "<p>Email generation disabled (API Key missing).</p>"
            
        try:
            subject = ticket.get('subject', 'No Subject')
            body = ticket.get('body', 'No Content')
            user_name = ticket.get('user_name', 'Valued Customer')
            
            prompt = f"""
            You are an AI assistant for an IT support system. Write a professional, empathetic, and clear email response to a user regarding their support ticket.
            
            TICKET DETAILS:
            Subject: {subject}
            User Issue: {body}
            Ticket Status: {ticket.get('status', 'Processing')}
            
            ADDITIONAL CONTEXT:
            {context}
            
            INSTRUCTIONS:
            - Address the user as "{user_name}".
            - Acknowledge the issue and provide reassurance.
            - If resolved, explain the resolution clearly.
            - If in progress, explain the next steps.
            - Use HTML formatting (paragraphs <p>, lists <ul>/<li>, bold <strong>).
            - Do NOT include the subject line in the body.
            - Sign off as "The ECE Support Team".
            - Keep it concise but helpful.
            
            EMAIL BODY (HTML):
            """
            
            response = self.model.generate_content(prompt)
            return response.text
            
        except Exception as e:
            logger.error(f"Error generating email content: {e}")
            return f"<p>Error generating email content. Please contact support manually.</p>"

if __name__ == "__main__":
    # Test
    import os
    # Mock config for standalone test
    class MockConfig:
        GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
        GENAI_EMAIL_MODEL = 'gemini-pro'
    config = MockConfig()
    
    generator = GoogleGenAIEmailGenerator()
    if generator.enabled:
        ticket = {
            'subject': 'Cannot login to VPN',
            'body': 'I keep getting error 691 when trying to connect.',
            'user_name': 'John Doe',
            'status': 'In Progress'
        }
        print(generator.generate_email_content(ticket, "We are investigating the issue with the VPN server."))
    else:
        print("Generator disabled.")
