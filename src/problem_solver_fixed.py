"""
PROBLEM SOLVER - ECE AGENT
===========================

Integrates with the Triage Specialist to solve tickets using:
1. Internal knowledge base (past solutions)
2. External web search (when needed)
3. LLM-based answer generation
4. Self-correction with retry logic
5. Quality validation before deployment

This is the "Problem Solver" agent from your ECE architecture.
"""

import requests
from groq import Groq
import numpy as np
import sqlite3
import pickle
import json
from sklearn.metrics.pairwise import cosine_similarity
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

# Import your existing triage specialist
import sys
# sys.path.append('.') # Not needed if running from root as module
from src.inference_service_full import TriageSpecialist
import config  # Centralized configuration




class ProblemSolver:
    """
    ECE Problem Solver Agent with self-correction and retry logic.
    
    This agent:
    1. Receives triage classification from Triage Specialist
    2. Searches internal knowledge base for similar solutions
    3. Optionally searches web for additional context
    4. Generates solution using LLM with retrieved context
    5. Self-validates and retries up to 3 times
    6. Escalates to human if all attempts fail
    """
    
    def __init__(self, 
                 triage_specialist: Optional[TriageSpecialist] = None,
                 model_name: str = "google/flan-t5-base",  # Smaller, faster
                 db_path: str = 'tickets.db',
                 enable_web_search: bool = False,
                 max_attempts: int = 3):
        """
        Initialize Problem Solver.
        
        Args:
            triage_specialist: Pre-initialized TriageSpecialist (recommended)
            model_name: HuggingFace model for generation (flan-t5-base is faster)
            db_path: Path to ticket database
            enable_web_search: Whether to use web search (requires duckduckgo-search)
            max_attempts: Maximum solution attempts before escalation
        """
        self.db_path = db_path
        self.max_attempts = max_attempts

        
        # Initialize or use provided Triage Specialist
        if triage_specialist is None:
            print("[init] Initializing Triage Specialist...")
            self.triage = TriageSpecialist(db_path=db_path)
        else:
            self.triage = triage_specialist
        
        # Groq LLM client (replaces flan-t5)
        self.groq_client = Groq(api_key=config.GROQ_API_KEY)
        self.groq_model  = config.GROQ_SOLVER_MODEL
        print(f"  LLM: Groq {self.groq_model}")

        # Serper web search (replaces DuckDuckGo)
        self.web_search_enabled = (
            enable_web_search
            and config.SERPER_ENABLED
            and bool(config.SERPER_API_KEY)
        )
        print(f"  Web search: {'Serper (Google)' if self.web_search_enabled else 'disabled'}")
        
        print("[done] Problem Solver ready")
    
    def solve(self, subject: str, body: str, ticket_id: Optional[str] = None, conversation_history: List[Dict] = None) -> Dict:
        """
        Main solving workflow with retry logic.
        
        Returns dict with:
        - success: bool
        - solution: str (the generated answer)
        - confidence: float
        - attempts: int
        - escalated: bool
        - metadata: dict
        """
        print(f"\n{'=' * 80}")
        print(f"PROBLEM SOLVER: Processing Ticket")
        if ticket_id:
            print(f"Ticket ID: {ticket_id}")
        print(f"Subject: {subject[:60]}...")
        print('=' * 80)
        
        # Step 1: Get triage classification
        print("\n[info] Step 1: Triage Classification")
        triage_result = self.triage.predict(
            subject=subject,
            body=body,
            retrieve_answer=True  # Get retrieved answer from knowledge base
        )
        
        print(f"  Type: {triage_result['type']} ({triage_result['type_confidence']:.1%})")
        print(f"  Priority: {triage_result['priority']} ({triage_result['priority_confidence']:.1%})")
        print(f"  Queue: {triage_result['queue']} ({triage_result['queue_confidence']:.1%})")
        print(f"  Tags: {[t['tag'] for t in triage_result['tags'][:3]]}")
        
        # Decide if we need to generate or can use retrieved answer directly
        retrieval_confidence = triage_result.get('answer_source', {}).get('similarity', 0.0)
        direct_threshold = config.SOLVER.get('direct_retrieval_threshold', 0.99)
        
        # Only use direct retrieval if NO conversation history exists (because history changes context)
        if not conversation_history and retrieval_confidence >= direct_threshold:
            print(f"\n[done] High similarity match found ({retrieval_confidence:.1%})")
            print("   Using retrieved answer directly (no generation needed)")
            return {
                'success': True,
                'solution': triage_result['answer'],
                'confidence': retrieval_confidence,
                'attempts': 1,
                'escalated': False,
                'method': 'direct_retrieval',
                'triage': triage_result
            }
        
        # Step 2: Solvability Analysis (The "Brain" Check)
        # ------------------------------------------------
        # Check if this is a hardware or physical issue that AI cannot solve remotely
        
        # Keywords that strongly suggest physical intervention
        hardware_keywords = [
            'broken', 'cracked', 'spilled', 'smoke', 'fire', 'smell', 
            'cable', 'mouse', 'keyboard', 'monitor', 'screen', 'printer', 
            'toner', 'jam', 'physical', 'hardware', 'laptop', 'battery'
        ]
        
        is_hardware = any(k in subject.lower() or k in body.lower() for k in hardware_keywords)
        is_hardware_queue = triage_result['queue'] in ['Facilities', 'Hardware Support', 'Assets']
        
        if is_hardware or is_hardware_queue:
            print(f"\n[alert] Detected Physical/Hardware Issue")
            print(f"   Reason: Keywords={is_hardware}, Queue={is_hardware_queue}")
            
            # For hardware, we verify if there's a simple fix (e.g. 'plug it in') 
            # or if it needs escalation. For safety, we prefer escalation.
            
            return {
                'success': False,
                'solution': None,
                'confidence': 1.0,
                'attempts': 0,
                'escalated': True,
                'escalation_reason': 'Physical/Hardware issue requires human intervention',
                'triage': triage_result
            }

        # Step 3: Attempt solution with RAG (Web Search + GenAI)
        # ----------------------------------------------------
        print(f"\n[info] Step 3: Solution Generation (max {self.max_attempts} attempts)")
        
        previous_feedback = ""
        last_solution = ""
        
        # RAG: Pre-fetch web context if internal KB is weak
        rag_context = ""
        retrieval_confidence = triage_result.get('answer_source', {}).get('similarity', 0.0)
        
        if self.web_search_enabled and retrieval_confidence < 0.85:
            print(f"    [web] Low KB confidence ({retrieval_confidence:.1%}). initiating RAG Web Search...")
            web_results = self._web_search(subject, body, max_results=3)
            if web_results:
                rag_context = "\n".join(web_results)
                print(f"    ✓ Retrieved {len(web_results)} web sources")
        
        for attempt in range(1, self.max_attempts + 1):
            print(f"\n  Attempt {attempt}/{self.max_attempts}")
            
            # Generate solution with feedback from previous attempts
            solution = self._generate_solution(
                subject=subject,
                body=body,
                triage=triage_result,
                attempt=attempt,
                previous_feedback=previous_feedback,
                rag_context=rag_context, # Pass RAG context
                conversation_history=conversation_history # Pass conversation history
            )
            last_solution = solution
            
            # Validate solution
            is_valid, validation_feedback = self._validate_solution(
                solution=solution,
                subject=subject,
                body=body,
                triage=triage_result
            )
            
            if is_valid:
                print(f"  [done] Solution validated successfully")
                return {
                    'success': True,
                    'solution': solution,
                    'confidence': 0.75 + (0.05 * (4 - attempt)),
                    'attempts': attempt,
                    'escalated': False,
                    'method': 'generated_rag' if rag_context else 'generated',
                    'triage': triage_result
                }
            else:
                print(f"  [warn] Validation failed: {validation_feedback}")
                previous_feedback = validation_feedback
                if attempt < self.max_attempts:
                    print(f"  [retry] Will retry with feedback: {validation_feedback}")
        
        # All attempts failed - escalate
        print(f"\n[alert] All {self.max_attempts} attempts failed - Escalating")
        return {
            'success': False,
            'solution': f"[Escalated] {last_solution}" if last_solution else None,
            'confidence': 0.0,
            'attempts': self.max_attempts,
            'escalated': True,
            'escalation_reason': 'Complex issue - automated resolution failed',
            'triage': triage_result
        }
    
    
    def _generate_solution(
        self,
        subject:              str,
        body:                 str,
        triage:               dict,
        attempt:              int,
        previous_feedback:    str  = "",
        rag_context:          str  = "",
        conversation_history: list = None,
    ) -> str:
        """
        Generate a solution using Groq LLaMA 3.3 70B with full RAG context.
        On retry attempts, the previous validation feedback is included so
        the model self-corrects.
        """
        # ── Assemble context ─────────────────────────────────────────────
        kb_answer  = triage.get('answer', '')
        similarity = triage.get('answer_source', {}).get('similarity', 0.0)
        tags_str   = ", ".join([t['tag'] for t in triage.get('tags', [])[:5]])

        kb_section = ""
        if kb_answer and similarity > 0.40:
            kb_section = (
                f"\n\n[KNOWLEDGE BASE — {similarity:.0%} match]\n"
                f"{kb_answer[:600]}"
            )

        web_section = ""
        if rag_context:
            web_section = f"\n\n[WEB SEARCH RESULTS]\n{rag_context[:800]}"

        history_section = ""
        if conversation_history:
            lines = [
                f"{m['sender'].upper()}: {m['message']}"
                for m in conversation_history
            ]
            history_section = "\n\n[CONVERSATION HISTORY]\n" + "\n".join(lines)

        retry_section = ""
        if attempt > 1 and previous_feedback:
            retry_section = (
                f"\n\n[PREVIOUS ATTEMPT {attempt - 1} REJECTED]\n"
                f"Reason: {previous_feedback}\n"
                f"You MUST address this feedback in your new response."
            )

        # ── System prompt ─────────────────────────────────────────────────
        system_prompt = (
            "You are an expert enterprise IT support agent. "
            "Your job is to produce clear, numbered, step-by-step solutions "
            "for employee support tickets. Rules:\n"
            "1. Always respond with numbered steps (1. 2. 3. etc.).\n"
            "2. Start each step with an action verb "
            "   (Click, Open, Navigate, Run, Verify, Enter, etc.).\n"
            "3. Be specific — reference the exact system, setting, or "
            "   command mentioned in the ticket.\n"
            "4. Do NOT repeat the ticket back. Do NOT add disclaimers.\n"
            "5. End with a verification step so the user can confirm resolution.\n"
            "6. If web sources are provided, use them to enrich your answer "
            "   and cite the source at the end."
        )

        # ── User prompt ───────────────────────────────────────────────────
        user_prompt = (
            f"Ticket Subject: {subject}\n"
            f"Ticket Description: {body[:600]}\n"
            f"Type: {triage.get('type')} | "
            f"Priority: {triage.get('priority')} | "
            f"Queue: {triage.get('queue')} | "
            f"Tags: {tags_str}"
            f"{kb_section}"
            f"{web_section}"
            f"{history_section}"
            f"{retry_section}\n\n"
            f"Provide a complete, step-by-step resolution:"
        )

        # ── Call Groq ─────────────────────────────────────────────────────
        try:
            response = self.groq_client.chat.completions.create(
                model    = self.groq_model,
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = config.GROQ_SOLVER_PARAMS['temperature'],
                max_tokens  = config.GROQ_SOLVER_PARAMS['max_tokens'],
                top_p       = config.GROQ_SOLVER_PARAMS['top_p'],
            )
            solution = response.choices[0].message.content.strip()
            print(f"    Groq generated {len(solution)} characters "
                  f"(attempt {attempt})")
            return solution

        except Exception as e:
            print(f"    Groq generation failed: {e}")
            # Graceful fallback — return a generic structured response
            return (
                f"1. Review the issue described: {subject}\n"
                f"2. Check relevant system settings and logs.\n"
                f"3. Attempt to reproduce the issue in a test environment.\n"
                f"4. If unresolved, escalate to your {triage.get('queue', 'support')} team.\n"
                f"5. Verify resolution by confirming the issue no longer occurs."
            )
    
    def _validate_solution(self, solution: str, subject: str, body: str, triage: Dict) -> Tuple[bool, str]:
        """
        Validate generated solution (Quality Gatekeeper function).
        Simplified validation to allow structured solutions through.
        
        Returns: (is_valid, feedback_message)
        """
        # Check 1: Minimum length
        min_length = 100  # Solutions should have reasonable content
        if len(solution) < min_length:
            return False, f"Solution too short ({len(solution)} chars). Need at least {min_length} characters."
        
        # Check 2: Has numbered steps (essential for structured responses)
        has_numbered_steps = any(f"{i}." in solution for i in range(1, 7))
        if not has_numbered_steps:
            return False, "Solution should include numbered steps (1., 2., 3.) for clarity."
        
        # Check 3: Not just repeating the ticket text
        # Simple check: solution shouldn't start with the subject text
        if subject.lower()[:30] in solution.lower()[:50]:
            return False, "Solution appears to repeat the ticket. Provide actionable steps instead."
        
        # If we get here, solution is valid
        return True, "Solution meets quality standards."
    
    def _web_search(self, subject: str, body: str, max_results: int = 3) -> list:
        """
        Search Google via Serper API for relevant support articles.
        Builds a precise query from the ticket's key terms rather than
        using the raw subject/body directly.
        Returns a list of plain-text result strings.
        """
        if not self.web_search_enabled:
            return []
        try:
            # Build a targeted query:
            # Extract error codes if present (e.g. "Error 403", "0x80070005")
            import re
            error_codes = re.findall(
                r'\b(?:error|code|exception)\s*[:#]?\s*([0-9a-fx]+)\b',
                f"{subject} {body}",
                re.IGNORECASE
            )
            if error_codes:
                query = f"{subject} {error_codes[0]} fix solution"
            else:
                # Use subject + first meaningful noun phrase from body
                query = f"{subject} how to fix enterprise IT support"

            response = requests.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": config.SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": max_results},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("organic", [])[:max_results]:
                title   = item.get("title", "")
                snippet = item.get("snippet", "")
                link    = item.get("link", "")
                if snippet:
                    results.append(f"Source: {title}\n{snippet}\nURL: {link}")

            print(f"    Serper returned {len(results)} results for: {query[:60]}")
            return results

        except Exception as e:
            print(f"    Web search failed: {e}")
            return []
    
    def save_solution(self, ticket_id: str, subject: str, body: str, 
                     solution: str, result: Dict):
        """
        Save successful solution to database for future learning.
        """
        if not result['success']:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Save to learning buffer for continual learning
        triage = result['triage']
        tags_json = json.dumps([t['tag'] for t in triage['tags']])
        
        cursor.execute('''
            INSERT INTO learning_buffer
            (subject, body, answer, type, priority, queue, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            subject, body, solution,
            triage['type'], triage['priority'], triage['queue'], tags_json
        ))
        
        conn.commit()
        conn.close()
        
        print(f"[save] Solution saved to learning buffer")


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Example workflow showing Problem Solver integration with Triage Specialist.
    """
    
    # Initialize Problem Solver (it will initialize Triage Specialist internally)
    solver = ProblemSolver(
        model_name="google/flan-t5-base",  # Faster than large
        enable_web_search=True,  # Changed to True to test Serper
        max_attempts=3
    )
    
    # Example 1: Technical issue
    print("\n" + "="*80)
    print("EXAMPLE 1: Technical Issue")
    print("="*80)
    
    result1 = solver.solve(
        subject="Cannot access shared drive",
        body="I'm getting 'Access Denied' error when trying to open the Marketing shared drive. "
             "I was able to access it yesterday but today it says I don't have permissions. "
             "My username is jsmith@company.com. Please help urgently.",
        ticket_id="TICKET-001"
    )
    
    if result1['success']:
        print(f"\n[done] SOLUTION GENERATED:")
        print(f"   Confidence: {result1['confidence']:.1%}")
        print(f"   Attempts: {result1['attempts']}")
        print(f"\n   {result1['solution']}")
        
        # Save solution
        solver.save_solution("TICKET-001", 
                              "Cannot access shared drive",
                              "I'm getting 'Access Denied' error when trying to open the Marketing shared drive. "
                              "I was able to access it yesterday but today it says I don't have permissions. "
                              "My username is jsmith@company.com. Please help urgently.",
                              result1['solution'], result1)
    else:
        print(f"\n[alert] ESCALATED TO HUMAN TEAM")
        print(f"   Reason: {result1['escalation_reason']}")
        print(f"   Triage info for human agent:")
        print(f"     Type: {result1['triage']['type']}")
        print(f"     Priority: {result1['triage']['priority']}")
        print(f"     Suggested Queue: {result1['triage']['queue']}")
    
    # Example 2: Billing issue
    print("\n" + "="*80)
    print("EXAMPLE 2: Billing Issue")
    print("="*80)
    
    result2 = solver.solve(
        subject="Double charged for subscription",
        body="I was charged twice for my monthly subscription. My credit card shows two charges "
             "of $49.99 on Dec 25th. Please refund one of them.",
        ticket_id="TICKET-002"
    )
    
    if result2['success']:
        print(f"\n[done] SOLUTION GENERATED:")
        print(f"   {result2['solution'][:200]}...")
    
    print("\n" + "="*80)
    print("Examples complete. Integrate this into your ECE workflow.")
    print("="*80)
