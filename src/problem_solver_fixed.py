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

import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
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

# Try to import web search (optional dependency)
try:
    from duckduckgo_search import DDGS
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    print("⚠️  duckduckgo-search not installed. Web search disabled.")
    print("   Install with: pip install duckduckgo-search")
    WEB_SEARCH_AVAILABLE = False


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
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        # self.device = torch.device('cpu') # FORCE CPU FOR DEBUGGING
        print(f"    🔍 Debug: Device set to {self.device}")
        
        # Initialize or use provided Triage Specialist
        if triage_specialist is None:
            print("🔧 Initializing Triage Specialist...")
            self.triage = TriageSpecialist(db_path=db_path)
        else:
            self.triage = triage_specialist
        
        # Initialize LLM        # Loading tokenizer
        print(f"  Loading tokenizer ({model_name})...")
        self.gen_tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
        self.generator = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self.device)
        
        # Initialize web search if enabled
        self.web_search_enabled = enable_web_search and WEB_SEARCH_AVAILABLE
        if self.web_search_enabled:
            self.web_searcher = DDGS()
            print("🌐 Web search enabled")
        else:
            print("📚 Web search disabled (using internal KB only)")
        
        print("✅ Problem Solver ready")
    
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
        print("\n📋 Step 1: Triage Classification")
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
            print(f"\n✅ High similarity match found ({retrieval_confidence:.1%})")
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
            print(f"\n🚨 Detected Physical/Hardware Issue")
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
        print(f"\n🔄 Step 3: Solution Generation (max {self.max_attempts} attempts)")
        
        previous_feedback = ""
        last_solution = ""
        
        # RAG: Pre-fetch web context if internal KB is weak
        rag_context = ""
        retrieval_confidence = triage_result.get('answer_source', {}).get('similarity', 0.0)
        
        if self.web_search_enabled and retrieval_confidence < 0.85:
            print(f"    🌐 Low KB confidence ({retrieval_confidence:.1%}). initiating RAG Web Search...")
            web_query = f"{subject} {body[:50]} solution fix"
            web_results = self._web_search(web_query, max_results=3)
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
                print(f"  ✅ Solution validated successfully")
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
                print(f"  ⚠️  Validation failed: {validation_feedback}")
                previous_feedback = validation_feedback
                if attempt < self.max_attempts:
                    print(f"  🔄 Will retry with feedback: {validation_feedback}")
        
        # All attempts failed - escalate
        print(f"\n🚨 All {self.max_attempts} attempts failed - Escalating")
        return {
            'success': False,
            'solution': f"[Escalated] {last_solution}" if last_solution else None,
            'confidence': 0.0,
            'attempts': self.max_attempts,
            'escalated': True,
            'escalation_reason': 'Complex issue - automated resolution failed',
            'triage': triage_result
        }
    
    
    def _generate_solution(self, subject: str, body: str, triage: Dict, attempt: int, previous_feedback: str = "", rag_context: str = "", conversation_history: List[Dict] = None) -> str:
        """
        Generate solution using Enhanced Answer Generator for better quality.
        Delegates to the new specialized class which handles structured prompting.
        """
        # Lazy initialization of enhanced generator to save resources until needed
        if not hasattr(self, 'enhanced_generator'):
            try:
                from src.enhanced_answer_generator import EnhancedAnswerGenerator
                print("✨ Initializing Enhanced Answer Generator...")
                self.enhanced_generator = EnhancedAnswerGenerator(
                    model_name=config.GENERATOR_MODEL,
                    device=self.device
                )
            except Exception as e:
                print(f"⚠️ Could not load Enhanced Generator: {e}. Falling back to legacy generation.")
                self.enhanced_generator = None
        
        # Use Enhanced Generator if available
        if getattr(self, 'enhanced_generator', None):
            try:
                ticket_data = {
                    'subject': subject,
                    'body': body,
                    'type': triage['type'],
                    'priority': triage['priority'],
                    'queue': triage['queue']
                }
                
                # If retrying, parse feedback for the generator
                feedback_dict = None
                previous_sol = None
                
                if attempt > 1 and previous_feedback:
                    # Create a simple feedback object for the enhanced generator
                    feedback_dict = {
                        'errors': [previous_feedback],
                        'overall_score': 50 # Dummy score to trigger improvement logic
                    }
                    previous_sol = "Previous attempt rejected" # We don't have the full text easily here, but this triggers the logic
                
                print(f"    ✨ Using Enhanced Generator (Attempt {attempt})...")
                solution = self.enhanced_generator.generate_solution(
                    ticket=ticket_data,
                    validation_feedback=feedback_dict,
                    previous_solution=previous_sol
                )
                print(f"    ✓ Generated {len(solution)} characters")
                return solution
                
            except Exception as e:
                print(f"    ❌ Enhanced Generation Error: {e}. Falling back to legacy.")
                # Fall through to legacy code below
        
        # LEGACY GENERATION (Fallback)
        # ------------------------------------------------------------------
        # Get retrieved answer as context
        retrieved_answer = triage.get('answer', '')
        answer_source = triage.get('answer_source', {})
        similarity = answer_source.get('similarity', 0.0)
        
        # Get web context if enabled and similarity is low
        # Get web context (RAG)
        web_context = rag_context
        if not web_context and self.web_search_enabled and similarity < config.SOLVER.get('web_search_trigger', 0.70):
             # Fallback to late-binding search if not passed in
            print(f"    🌐 Searching web (low similarity: {similarity:.1%})...")
            web_results = self._web_search(f"{subject} {body[:100]}")
            if web_results:
                web_context = f"ADDITIONAL WEB CONTEXT:\n" + "\n".join([f"- {r}" for r in web_results[:2]])
        
        # Build tags string
        tags_str = ", ".join([t['tag'] for t in triage['tags'][:5]])
        
        # Build feedback section for retry attempts
        feedback_section = ""
        if attempt > 1 and previous_feedback:
            feedback_section = f"\nPREVIOUS ATTEMPT FEEDBACK (Attempt {attempt-1} was rejected):\n{previous_feedback}\nPlease address this feedback in your new response.\n"
        
        # Build history string
        history_str = ""
        if conversation_history:
             history_str = "\nCONVERSATION HISTORY:\n" + "\n".join([f"{msg['sender'].upper()}: {msg['message']}" for msg in conversation_history]) + "\n"

        # Use config's improved prompt template
        prompt = config.SOLUTION_PROMPT_TEMPLATE.format(
            subject=subject,
            body=body[:500],  # More context
            ticket_type=triage['type'],
            priority=triage['priority'],
            queue=triage['queue'],
            tags=tags_str,
            similarity=f"{similarity:.0%}",
            retrieved_answer=retrieved_answer[:500] if retrieved_answer else 'No similar solution found in knowledge base.',
            web_context=web_context,
            previous_feedback=feedback_section,
            history=history_str
        )
        
        # Restore real prompt
        inputs = self.gen_tokenizer(
            prompt,
            return_tensors="pt",
            max_length=config.GENERATION.get('max_input_length', 1024),
            truncation=True
        ).to(self.device)
        
        try:
            with torch.no_grad():
                outputs = self.generator.generate(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask, # Pass attention mask
                    decoder_start_token_id=0, # Hardcoded T5 start token (essential)
                    
                    max_length=config.GENERATION.get('max_output_length', 400),
                    min_length=config.GENERATION.get('min_output_length', 50),
                    num_beams=config.GENERATION.get('num_beams', 1), # Greedy for stability
                    temperature=config.GENERATION.get('temperature', 1.0),
                    do_sample=config.GENERATION.get('do_sample', False),
                )
            solution = self.gen_tokenizer.decode(outputs[0], skip_special_tokens=True)
            print(f"    ✓ Generated {len(solution)} characters")
            
        except Exception as e:
            print(f"    ❌ Generation Error: {e}")
            solution = ""
        
        # Fallback if generation failed (empty output or error)
        if len(solution.strip()) < 10:
            print("    ⚠️ Generation failed (empty output). Using fallback strategy.")
            if retrieved_answer:
                solution = f"Based on our knowledge base:\n\n{retrieved_answer}\n\n(Generated via fallback)"
            else:
                solution = "We acknowledge your issue. A support agent will review this ticket shortly as automated resolution was not possible."
                
        return solution.strip()
    
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
    
    def _web_search(self, query: str, max_results: int = 2) -> List[str]:
        """Search web for additional context (optional feature)."""
        if not self.web_search_enabled:
            return []
        
        try:
            results = self.web_searcher.text(query, max_results=max_results)
            if not results:
                return []
            return [f"{r['title']}: {r['body'][:150]}..." for r in results]
        except Exception as e:
            print(f"    ⚠️  Web search error: {e}")
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
        
        print(f"💾 Solution saved to learning buffer")


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
        enable_web_search=False,  # Disable for internal IT tickets
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
        print(f"\n✅ SOLUTION GENERATED:")
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
        print(f"\n🚨 ESCALATED TO HUMAN TEAM")
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
        print(f"\n✅ SOLUTION GENERATED:")
        print(f"   {result2['solution'][:200]}...")
    
    print("\n" + "="*80)
    print("Examples complete. Integrate this into your ECE workflow.")
    print("="*80)
