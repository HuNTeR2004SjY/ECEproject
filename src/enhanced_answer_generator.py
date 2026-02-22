"""
Enhanced Answer Generator - Optimized for Quality Solutions
Produces high-quality, step-by-step solutions that directly address ticket issues
"""

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch
from typing import Dict, List, Optional
import re


class EnhancedAnswerGenerator:
    """
    Improved answer generator with advanced prompt engineering
    to produce actionable, structured solutions
    """
    
    def __init__(self, model_name: str = "google/flan-t5-large", device = None):
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        print(f"🧠 Loading Enhanced Answer Generator on {self.device}...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self.device)
        
        # Action verbs that make solutions actionable
        self.action_verbs = [
            'click', 'select', 'navigate', 'enter', 'submit', 'open',
            'go to', 'access', 'verify', 'check', 'confirm', 'review',
            'update', 'change', 'modify', 'install', 'configure',
            'enable', 'disable', 'save', 'delete', 'reset'
        ]
        print("✅ Enhanced Answer Generator ready")
        
    def create_structured_prompt(self, ticket: Dict) -> str:
        """
        Create a prompt with category-specific solution templates.
        """
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        queue = ticket.get('queue', 'General Support').lower()
        
        # Detect category and provide appropriate template
        combined_text = (subject + " " + description).lower()
        
        if any(word in combined_text for word in ['charge', 'bill', 'payment', 'refund', 'invoice', 'subscription', 'price', 'fee', 'money']):
            category = "billing"
            template = """For billing issues, follow these steps:
1. Log into your account at billing.company.com
2. Go to Billing History or Transaction History
3. Find the transaction in question
4. Click Request Refund or Dispute Charge
5. Fill out the form with transaction details
6. Submit and wait 3-5 business days for processing"""
        elif any(word in combined_text for word in ['password', 'login', 'access', 'account', 'locked', 'reset', 'sign in', 'username']):
            category = "account"
            template = """For account access issues, follow these steps:
1. Go to the login page and click Forgot Password
2. Enter your registered email address
3. Check your email including spam folder
4. Click the password reset link
5. Create a new password following the requirements
6. Log in with your new credentials"""
        elif any(word in combined_text for word in ['slow', 'crash', 'error', 'not working', 'frozen', 'bug', 'install', 'update']):
            category = "technical"
            template = """For technical issues, follow these steps:
1. Save any unsaved work and close the application
2. Restart the application
3. If the issue persists, clear cache or reinstall
4. Check for available updates and install them
5. Restart your computer
6. Contact IT support if still not resolved"""
        elif any(word in combined_text for word in ['network', 'internet', 'wifi', 'vpn', 'connection', 'ethernet', 'connect']):
            category = "network"
            template = """For network issues, follow these steps:
1. Check if other devices can connect to the network
2. Restart your router or modem
3. Disconnect and reconnect to the network
4. Run network troubleshooter (Settings > Network > Troubleshoot)
5. Check network adapter settings
6. Contact IT if the issue persists"""
        else:
            category = "general"
            template = """For general support issues, follow these steps:
1. Document the exact issue and any error messages
2. Try restarting the affected application or service
3. Check our FAQ section for common solutions
4. Submit a detailed support ticket if needed
5. Our team will respond within 24 hours
6. Keep your ticket number for reference"""
        
        prompt = f"""You are an expert support agent. Generate a solution for this customer issue.

TICKET INFORMATION:
Subject: {subject}
Description: {description[:250]}
Category: {category}

REFERENCE TEMPLATE (adapt this to the specific issue):
{template}

Now write a customized solution for this specific customer issue. Use the template as a guide but make it relevant to their exact problem. Write 5-6 clear steps.

CUSTOMIZED SOLUTION:
1."""

        return prompt
    
    def create_feedback_enhanced_prompt(self, 
                                       ticket: Dict, 
                                       previous_solution: str,
                                       validation_feedback: Dict) -> str:
        """
        Create improved prompt based on previous attempt feedback
        """
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        
        # Extract specific issues from feedback
        errors = validation_feedback.get('errors', [])
        score = validation_feedback.get('overall_score', 0)
        
        prompt = f"""PREVIOUS SOLUTION REJECTED (Score: {score}/100)
Issues: {', '.join(errors[:3])}

TICKET:
Subject: {subject}
Description: {description[:300]}

Generate an IMPROVED solution that:
- Contains 5+ numbered steps
- Uses action verbs: click, open, navigate, enter, verify, check
- Is specific to: {self._extract_key_terms(subject)}
- Does NOT repeat the ticket
- Includes verification at the end

IMPROVED SOLUTION:
To resolve this issue:

1."""

        return prompt
    
    def generate_solution(self, 
                         ticket: Dict,
                         validation_feedback: Optional[Dict] = None,
                         previous_solution: Optional[str] = None) -> str:
        """
        Generate solution using AI model with template fallback.
        Uses feedback from Quality Gatekeeper for self-correction on retries.
        """
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        
        # Try AI generation first
        try:
            # Build prompt based on whether this is a retry
            if validation_feedback and previous_solution:
                print(f"    🔄 Retry with feedback: {validation_feedback.get('errors', ['Unknown'])}")
                prompt = self._build_retry_prompt(ticket, validation_feedback)
            else:
                print(f"    🤖 AI generating solution for: {subject[:40]}...")
                prompt = self._build_initial_prompt(ticket)
            
            # Tokenize
            inputs = self.tokenizer(
                prompt,
                return_tensors="pt",
                max_length=1024,
                truncation=True
            ).to(self.device)
            
            # Generate
            with torch.no_grad():
                outputs = self.model.generate(
                    inputs.input_ids,
                    attention_mask=inputs.attention_mask,
                    max_length=400,
                    min_length=50,
                    num_beams=4,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    repetition_penalty=2.0,
                    no_repeat_ngram_size=3,
                    early_stopping=True
                )
            
            solution = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            print(f"    ✓ AI generated {len(solution)} characters")
            
            # If AI output is too short or nonsensical, use template
            if len(solution.strip()) < 50 or not any(f"{i}." in solution for i in range(1, 5)):
                print(f"    ⚠️ AI output insufficient, using template fallback")
                solution = self._get_template_solution(ticket)
            else:
                # Clean up AI output
                solution = self._format_ai_output(solution, subject)
            
            return solution
            
        except Exception as e:
            print(f"    ❌ AI generation error: {e}, using template")
            return self._get_template_solution(ticket)
    
    def _build_initial_prompt(self, ticket: Dict) -> str:
        """Build prompt for initial solution attempt."""
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        
        return f"""You are an IT support agent. Provide a step-by-step solution.

TICKET:
Subject: {subject}
Description: {description[:300]}

Write a clear solution with numbered steps (1., 2., 3., etc.) to resolve this issue.
Do not repeat the ticket description. Provide actionable steps only.

Solution:
1."""
    
    def _build_retry_prompt(self, ticket: Dict, feedback: Dict) -> str:
        """Build prompt for retry with Quality Gatekeeper feedback."""
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        errors = feedback.get('errors', ['Solution rejected'])
        
        return f"""Previous solution was REJECTED.
Feedback: {', '.join(errors) if isinstance(errors, list) else errors}

TICKET:
Subject: {subject}
Description: {description[:200]}

Write an IMPROVED solution that addresses the feedback.
Provide 5-6 numbered steps (1., 2., 3., etc.) with clear actions.

Improved Solution:
1."""
    
    def _format_ai_output(self, solution: str, subject: str) -> str:
        """Format and clean AI output."""
        import re
        
        # Remove any accidental ticket repetition at start
        if subject.lower()[:20] in solution.lower()[:60]:
            lines = solution.split('\n')
            solution = '\n'.join(lines[1:]) if len(lines) > 1 else solution
        
        # Ensure it starts properly
        solution = solution.strip()
        if not solution.startswith('1.') and not solution.lower().startswith('to resolve'):
            solution = "To resolve this issue:\n\n" + solution
        
        # Add intro if starts directly with step
        if solution.startswith('1.'):
            solution = "To resolve this issue:\n\n" + solution
        
        return solution
    
    def _get_template_solution(self, ticket: Dict) -> str:
        """Get a template-based solution as fallback."""
        subject = ticket.get('subject', '')
        description = ticket.get('description', ticket.get('body', subject))
        combined_text = (subject + " " + description).lower()
        
        # Detect category and return appropriate template
        if any(word in combined_text for word in ['charge', 'bill', 'payment', 'refund', 'invoice', 'subscription']):
            return """To resolve your billing issue:

1. Log into your account at the customer portal.
2. Navigate to Billing > Transaction History.
3. Locate the transaction in question.
4. Click 'Request Refund' or 'Dispute Charge'.
5. Fill out the form with transaction details.
6. Submit - our team will process within 3-5 business days."""

        elif any(word in combined_text for word in ['password', 'login', 'access', 'account', 'locked']):
            return """To resolve your account access issue:

1. Go to the login page and click 'Forgot Password'.
2. Enter your registered email address.
3. Check your email (including spam) for the reset link.
4. Click the link and create a new password.
5. Log in with your new credentials.
6. Contact support if still unable to access."""

        elif any(word in combined_text for word in ['vpn', 'network', 'wifi', 'connection', 'internet']):
            return """To resolve your network issue:

1. Disconnect from the current network/VPN.
2. Restart your router (unplug for 30 seconds).
3. Reconnect to the network.
4. If using VPN, restart the VPN client.
5. Run network troubleshooter in Settings.
6. Contact IT if issue persists."""

        elif any(word in combined_text for word in ['error', 'crash', 'not working', 'slow', 'frozen']):
            return """To resolve this technical issue:

1. Save your work and close the application.
2. Restart the application.
3. If issue persists, clear the application cache.
4. Check for and install any updates.
5. Restart your computer.
6. If still not working, reinstall the application."""

        else:
            return """To resolve your issue:

1. Document the exact issue and any error messages.
2. Restart the affected application or service.
3. Check our FAQ section for similar issues.
4. If unresolved, submit a support ticket with details.
5. Our team will respond within 24 hours.
6. For urgent issues, contact support directly."""
    
    def _extract_key_terms(self, text: str) -> str:
        """Extract key terms from text"""
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'can', 'cannot',
                     'how', 'to', 'i', 'my', 'me', 'need', 'want', 'help', 'about',
                     'please', 'would', 'could', 'should', 'with', 'for', 'from'}
        
        words = re.sub(r'[^\w\s]', '', text.lower()).split()
        key_words = [w for w in words if w not in stop_words and len(w) > 2]
        
        return ' '.join(key_words[:6])
    
    def _post_process_solution(self, solution: str, ticket: Dict) -> str:
        """
        Post-process solution to clean up common model output issues.
        """
        import re
        
        # Get ticket text to detect repetition
        ticket_text = ticket.get('body', ticket.get('description', '')).lower()[:100]
        subject_text = ticket.get('subject', '').lower()
        
        # Clean up the raw output first
        solution = solution.strip()
        
        # Remove common model artifacts
        solution = solution.replace("To resolve this issue:\n\n1. To resolve this issue:", "")
        solution = solution.replace("1. To resolve this issue:", "")
        
        # Split into lines for processing
        lines = solution.split('\n')
        cleaned_lines = []
        step_num = 1
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip lines that are just repeating meta-text
            if line.lower().startswith('to resolve this issue'):
                continue
            if line.lower().startswith('solution:'):
                continue
            
            # Remove existing numbering
            clean_line = re.sub(r'^[\d]+[\.\)\:]?\s*', '', line).strip()
            
            # Skip empty lines after cleaning
            if not clean_line:
                continue
            
            # Skip lines that are just numbers
            if clean_line.isdigit():
                continue
            
            # Add proper step number (removed aggressive filtering - let the model output through)
            cleaned_lines.append(f"{step_num}. {clean_line}")
            step_num += 1
        
        # Build final solution
        if len(cleaned_lines) >= 3:
            solution = "To resolve this issue:\n\n" + "\n".join(cleaned_lines)
        else:
            # Use category-specific fallback instead of generic
            solution = self._get_category_fallback(ticket)
        
        return solution
    
    def _get_category_fallback(self, ticket: Dict) -> str:
        """Return category-specific fallback when model output is insufficient."""
        combined_text = (ticket.get('subject', '') + " " + ticket.get('body', ticket.get('description', ''))).lower()
        
        if any(word in combined_text for word in ['charge', 'bill', 'payment', 'refund', 'invoice', 'subscription']):
            return """To resolve this billing issue:

1. Log into your account at our billing portal.
2. Navigate to Transaction History or Billing section.
3. Locate the transaction(s) in question.
4. Click on "Request Refund" or "Dispute Charge" button.
5. Fill out the refund request form with your transaction details.
6. Submit the form - our billing team will process within 3-5 business days."""

        elif any(word in combined_text for word in ['password', 'login', 'access', 'account', 'locked']):
            return """To resolve this account access issue:

1. Go to the login page and click "Forgot Password".
2. Enter your registered email address.
3. Check your email inbox (and spam folder) for the reset link.
4. Click the reset link and create a new password.
5. Use the new password to log into your account.
6. If still locked, contact support with your account details."""

        elif any(word in combined_text for word in ['vpn', 'network', 'wifi', 'internet', 'connection']):
            return """To resolve this network issue:

1. Disconnect from the current network.
2. Restart your router or modem (wait 30 seconds).
3. Reconnect to the network.
4. If using VPN, restart the VPN client application.
5. Run the network troubleshooter in your device settings.
6. Contact IT support if the issue persists."""

        elif any(word in combined_text for word in ['error', 'crash', 'not working', 'frozen', 'slow']):
            return """To resolve this technical issue:

1. Save any unsaved work and close the application.
2. Restart the application.
3. If the issue persists, clear the application cache.
4. Check for available software updates and install them.
5. Restart your computer.
6. If still not working, reinstall the application or contact IT support."""

        else:
            return """To resolve this issue:

1. Document the exact issue with any error messages or screenshots.
2. Try restarting the affected application or service.
3. Check our FAQ section at support.company.com for common solutions.
4. If the issue persists, submit a support ticket with full details.
5. Our support team will respond within 24 hours.
6. For urgent issues, contact us by phone or live chat."""

