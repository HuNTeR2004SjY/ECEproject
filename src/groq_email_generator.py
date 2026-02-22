"""
Groq Email Generator
====================

Generates professional email content using Groq's fast inference API.
"""

import logging
import os
from typing import Dict, Optional
import groq
import config

logger = logging.getLogger(__name__)

class GroqEmailGenerator:
    """
    Generates email content using Groq's API.
    """
    
    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        """
        Initialize the generator.
        
        Args:
            api_key: Groq API Key (optional, defaults to config)
            model_name: Model to use (default: config.GROQ_EMAIL_MODEL)
        """
        self.api_key = api_key or config.GROQ_API_KEY
        self.model_name = model_name or config.GROQ_EMAIL_MODEL
        
        if not self.api_key:
            logger.warning("GROQ_API_KEY not found. Email generation will be disabled.")
            self.enabled = False
        else:
            try:
                self.client = groq.Groq(api_key=self.api_key)
                self.enabled = True
                logger.info(f"Groq Email Generator initialized with model {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize Groq client: {e}")
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
            ticket_status = ticket.get('status', 'Processing')
            
            prompt = f"""
            You are an AI assistant for an IT support system. Write a professional, empathetic, and clear email response to a user regarding their support ticket.
            
            TICKET DETAILS:
            Subject: {subject}
            User Issue: {body}
            Ticket Status: {ticket_status}
            
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
            
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=self.model_name,
            )
            
            return chat_completion.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error generating email content with Groq: {e}")
            return f"<p>Error generating email content. Please contact support manually.</p>"

if __name__ == "__main__":
    # Test
    # Mock config for standalone test
    class MockConfig:
        GROQ_API_KEY = os.getenv('GROQ_API_KEY')
        GROQ_EMAIL_MODEL = 'llama3-70b-8192'
    config = MockConfig()
    
    generator = GroqEmailGenerator()
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
