"""
Enterprise Context Engine - Workflow Manager
============================================
Central orchestration hub that manages the full ticket processing pipeline:
  1. Triage (classification via ML model)
  2. Problem Solving (AI generation with self-correction loop)
  3. Automation (notifications, escalation, state tracking)
  4. Audit Logging (all events persisted to database)

Usage:
    from Workflow_Manager import WorkflowManager

    wm = WorkflowManager()
    result = wm.process_ticket(
        subject="Cannot access shared drive",
        body="Getting Access Denied error...",
        user_email="user@example.com",
        user_id="42"
    )
"""

import logging
import sqlite3
import json
import uuid
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

import sys
sys.path.append('.')

import config
from src.inference_service_full import TriageSpecialist
from src.problem_solver_fixed import ProblemSolver
from src.automation_specialist import AutomationSpecialist

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

class TicketStatus(Enum):
    RECEIVED       = "received"
    TRIAGING       = "triaging"
    KNOWN_SOLUTION = "known_solution"
    SOLVING        = "solving"
    VALIDATING     = "validating"
    RESOLVED       = "resolved"
    ESCALATED      = "escalated"
    FAILED         = "failed"


@dataclass
class Ticket:
    """Represents a ticket flowing through the workflow pipeline."""
    id: str
    subject: str
    body: str
    user_id: str
    user_email: str
    company_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    status: TicketStatus = TicketStatus.RECEIVED
    priority: str = "Medium"
    category: str = "General"
    queue: str = ""
    tags: List[str] = field(default_factory=list)
    attempt_count: int = 0
    resolution: Optional[str] = None
    triage_result: Optional[Dict] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    """Final result returned after processing a ticket through the pipeline."""
    ticket_id: str
    success: bool
    resolution: Optional[str]
    escalated: bool = False
    escalation_reason: Optional[str] = None
    actions_taken: List[str] = field(default_factory=list)
    total_attempts: int = 0
    duration_seconds: float = 0.0
    triage: Optional[Dict] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            'ticket_id': self.ticket_id,
            'success': self.success,
            'resolution': self.resolution,
            'escalated': self.escalated,
            'escalation_reason': self.escalation_reason,
            'actions_taken': self.actions_taken,
            'total_attempts': self.total_attempts,
            'duration_seconds': round(self.duration_seconds, 2),
            'triage': self.triage,
            'confidence': round(self.confidence * 100, 1),
        }


# ============================================================================
# AUDIT LOGGER
# ============================================================================

class AuditLogger:
    """Logs all workflow events to the database for traceability."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        self._ensure_table()

    def _ensure_table(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS workflow_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to create audit table: {e}")

    def log_event(self, ticket_id: str, event: str, details: Dict = None):
        """Log an event for a ticket."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                'INSERT INTO workflow_audit_log (ticket_id, event, details, timestamp) VALUES (?, ?, ?, ?)',
                (ticket_id, event, json.dumps(details or {}), datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
            logger.debug(f"[AUDIT] {ticket_id} | {event}")
        except Exception as e:
            logger.error(f"Audit log failed: {e}")

    def get_ticket_log(self, ticket_id: str) -> List[Dict]:
        """Retrieve all audit events for a ticket."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM workflow_audit_log WHERE ticket_id = ? ORDER BY id ASC',
                (ticket_id,)
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            return []


# ============================================================================
# WORKFLOW MANAGER
# ============================================================================

class WorkflowManager:
    """
    Central orchestrator for the Enterprise Context Engine.

    Coordinates the Triage Specialist, Problem Solver, and Automation Specialist
    to process tickets through a structured pipeline with retry logic and
    audit logging.
    """

    MAX_ATTEMPTS = 3

    def __init__(
        self,
        triage_specialist: TriageSpecialist = None,
        problem_solver: ProblemSolver = None,
        automation_specialist: AutomationSpecialist = None,
    ):
        """
        Initialize the Workflow Manager.

        Can accept pre-initialized agents (shared with app.py) or create its own.

        Args:
            triage_specialist: Pre-initialized TriageSpecialist (optional)
            problem_solver:    Pre-initialized ProblemSolver (optional)
            automation_specialist: Pre-initialized AutomationSpecialist (optional)
        """
        # Use provided agents or initialize new ones
        if triage_specialist and problem_solver:
            self.triage = triage_specialist
            self.solver = problem_solver
        else:
            logger.info("[WorkflowManager] Initializing Triage Specialist...")
            self.triage = TriageSpecialist(db_path=config.DATABASE_PATH)
            logger.info("[WorkflowManager] Initializing Problem Solver...")
            self.solver = ProblemSolver(
                triage_specialist=self.triage,
                model_name=config.GENERATOR_MODEL,
                enable_web_search=config.SOLVER['enable_web_search'],
                max_attempts=self.MAX_ATTEMPTS,
            )

        if automation_specialist:
            self.automation = automation_specialist
        else:
            self.automation = AutomationSpecialist(email_config=config.EMAIL_CONFIG)

        self.audit = AuditLogger()
        self._active_tickets: Dict[str, Ticket] = {}

        logger.info("[WorkflowManager] Initialized successfully.")

    # ── Public API ──────────────────────────────────────────────

    def process_ticket(
        self,
        subject: str,
        body: str,
        user_email: str,
        user_id: str = "unknown",
        company_id: Optional[int] = None,
        ticket_id: str = None,
    ) -> WorkflowResult:
        """
        Process a ticket through the full ECE pipeline.

        This is the main entry point. It:
          1. Creates a Ticket object
          2. Runs triage classification
          3. Runs the Problem Solver (with retry loop)
          4. Sends notifications via Automation Specialist
          5. Persists everything to the database
          6. Returns a WorkflowResult

        Args:
            subject:    Ticket subject line
            body:       Ticket description / body
            user_email: Email of the ticket submitter
            user_id:    User ID for tracking
            ticket_id:  Optional custom ticket ID (auto-generated if not provided)

        Returns:
            WorkflowResult with success status, resolution, and metadata
        """
        start_time = time.time()

        # Generate ticket ID if not provided
        if not ticket_id:
            ticket_id = f"WF-{uuid.uuid4().hex[:8].upper()}"

        # Create ticket object
        ticket = Ticket(
            id=ticket_id,
            subject=subject,
            body=body,
            user_id=user_id,
            user_email=user_email,
            company_id=company_id,
        )
        self._active_tickets[ticket.id] = ticket

        self.audit.log_event(ticket.id, "ticket_received", {
            "subject": subject[:100],
            "user_id": user_id,
        })

        try:
            result = self._run_pipeline(ticket)
        except Exception as exc:
            logger.exception(f"Unhandled error in pipeline for ticket {ticket.id}")
            self.audit.log_event(ticket.id, "pipeline_error", {"error": str(exc)})
            result = WorkflowResult(
                ticket_id=ticket.id,
                success=False,
                resolution=None,
                escalated=True,
                escalation_reason=f"System error: {exc}",
            )
        finally:
            result.duration_seconds = time.time() - start_time
            self._active_tickets.pop(ticket.id, None)
            self.audit.log_event(ticket.id, "pipeline_complete", {
                "success": result.success,
                "duration": round(result.duration_seconds, 2),
                "escalated": result.escalated,
            })

        return result

    def get_active_tickets(self) -> List[Dict]:
        """Get list of currently processing tickets."""
        return [
            {"id": t.id, "subject": t.subject, "status": t.status.value}
            for t in self._active_tickets.values()
        ]

    def get_ticket_audit_log(self, ticket_id: str) -> List[Dict]:
        """Get the full audit trail for a ticket."""
        return self.audit.get_ticket_log(ticket_id)

    # ── Pipeline ────────────────────────────────────────────────

    def _run_pipeline(self, ticket: Ticket) -> WorkflowResult:
        """Run the full ECE pipeline: Triage -> Solve -> Automate."""
        actions: List[str] = []

        # ── Step 1: Triage Classification ──
        self._update_status(ticket, TicketStatus.TRIAGING)
        self.audit.log_event(ticket.id, "triage_started")

        triage_result = self.triage.predict(
            subject=ticket.subject,
            body=ticket.body,
            retrieve_answer=True,
        )

        ticket.priority = triage_result['priority']
        ticket.queue = triage_result['queue']
        ticket.category = triage_result['queue']
        ticket.tags = [t['tag'] for t in triage_result.get('tags', [])[:5]]
        ticket.triage_result = triage_result

        actions.append(
            f"Triage: Type={triage_result['type']} "
            f"Priority={triage_result['priority']} "
            f"Queue={triage_result['queue']}"
        )
        self.audit.log_event(ticket.id, "triage_complete", {
            "type": triage_result['type'],
            "priority": triage_result['priority'],
            "queue": triage_result['queue'],
            "tags": ticket.tags,
        })
        logger.info(f"[{ticket.id}] Triage: {triage_result['type']} / {triage_result['priority']} / {triage_result['queue']}")

        # ── Step 2: Check KB for direct match ──
        retrieval_confidence = triage_result.get('answer_source', {}).get('similarity', 0.0)
        direct_threshold = config.SOLVER.get('direct_retrieval_threshold', 0.99)

        if retrieval_confidence >= direct_threshold:
            self._update_status(ticket, TicketStatus.KNOWN_SOLUTION)
            resolution = triage_result.get('answer', '')
            actions.append(f"Resolved via Knowledge Base (confidence: {retrieval_confidence:.0%})")
            self.audit.log_event(ticket.id, "kb_direct_match", {"confidence": retrieval_confidence})

            # Save to DB and notify
            self._save_ticket_to_db(ticket, resolution, "resolved", triage_result)
            self._notify_user(ticket, resolution, escalated=False)
            self._update_status(ticket, TicketStatus.RESOLVED)

            return WorkflowResult(
                ticket_id=ticket.id,
                success=True,
                resolution=resolution,
                actions_taken=actions,
                total_attempts=0,
                triage=self._flatten_triage(triage_result),
                confidence=retrieval_confidence,
            )

        # ── Step 3: Problem Solver (Agentic Loop) ──
        return self._agentic_loop(ticket, triage_result, actions)

    def _agentic_loop(self, ticket: Ticket, triage_result: Dict, actions: List[str]) -> WorkflowResult:
        """Run the Problem Solver with retry logic."""
        self._update_status(ticket, TicketStatus.SOLVING)
        self.audit.log_event(ticket.id, "solving_started")

        # Call the ProblemSolver which has its own internal retry loop
        solver_result = self.solver.solve(
            subject=ticket.subject,
            body=ticket.body,
            ticket_id=ticket.id,
        )

        attempts = solver_result.get('attempts', 1)
        ticket.attempt_count = attempts

        if solver_result.get('success'):
            # ── Solution approved ──
            resolution = solver_result['solution']
            confidence = solver_result.get('confidence', 0.75)
            method = solver_result.get('method', 'generated')

            actions.append(f"Solution generated via {method} (attempt {attempts}, confidence: {confidence:.0%})")
            self.audit.log_event(ticket.id, "solution_approved", {
                "attempt": attempts,
                "method": method,
                "confidence": confidence,
            })

            # Validate through automation
            self._update_status(ticket, TicketStatus.VALIDATING)
            actions.append("Solution validated")

            # Save and notify
            self._save_ticket_to_db(ticket, resolution, "solution_proposed", triage_result)
            self._notify_user(ticket, resolution, escalated=False)
            self._update_status(ticket, TicketStatus.RESOLVED)

            return WorkflowResult(
                ticket_id=ticket.id,
                success=True,
                resolution=resolution,
                actions_taken=actions,
                total_attempts=attempts,
                triage=self._flatten_triage(triage_result),
                confidence=confidence,
            )
        else:
            # ── Escalated ──
            reason = solver_result.get('escalation_reason', 'Automated resolution failed')
            solution_text = solver_result.get('solution')

            actions.append(f"Escalated after {attempts} attempts: {reason}")
            self.audit.log_event(ticket.id, "ticket_escalated", {
                "attempts": attempts,
                "reason": reason,
            })

            # Save and notify
            self._save_ticket_to_db(ticket, solution_text, "escalated", triage_result)
            self._notify_user(ticket, solution_text, escalated=True, reason=reason)
            self._update_status(ticket, TicketStatus.ESCALATED)

            return WorkflowResult(
                ticket_id=ticket.id,
                success=False,
                resolution=solution_text,
                escalated=True,
                escalation_reason=reason,
                actions_taken=actions,
                total_attempts=attempts,
                triage=self._flatten_triage(triage_result),
                confidence=0.0,
            )

    # ── Persistence ─────────────────────────────────────────────

    def _save_ticket_to_db(self, ticket: Ticket, solution: str, status: str, triage_result: Dict):
        """Save the ticket and its interactions to the database."""
        try:
            conn = sqlite3.connect(config.DATABASE_PATH)
            cursor = conn.cursor()

            tags_json = json.dumps(ticket.tags)

            cursor.execute('''
                INSERT OR REPLACE INTO classified_tickets 
                (id, subject, body, pred_type, pred_priority, pred_queue, 
                 timestamp, corrected, user_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''', (
                ticket.id, ticket.subject, ticket.body,
                triage_result['type'], triage_result['priority'], triage_result['queue'],
                datetime.now().isoformat(), ticket.user_id, status,
            ))

            # Save user message
            cursor.execute('''
                INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp)
                VALUES (?, 'user', ?, ?)
            ''', (ticket.id, ticket.body, datetime.now().isoformat()))

            # Save AI solution
            if solution:
                cursor.execute('''
                    INSERT INTO ticket_interactions (ticket_id, sender, message, timestamp)
                    VALUES (?, 'ai', ?, ?)
                ''', (ticket.id, solution, datetime.now().isoformat()))

            conn.commit()
            conn.close()
            logger.info(f"[{ticket.id}] Saved to database with status: {status}")

        except Exception as e:
            logger.error(f"[{ticket.id}] Failed to save to database: {e}")

    # ── Notifications ───────────────────────────────────────────

    def _notify_user(self, ticket: Ticket, solution: str, escalated: bool, reason: str = ""):
        """Send notifications via the Automation Specialist."""
        try:
            ticket_data = {
                'id': ticket.id,
                'subject': ticket.subject,
                'body': ticket.body,
                'type': ticket.triage_result.get('type', '') if ticket.triage_result else '',
                'priority': ticket.priority,
                'user_id': ticket.user_id,
                'company_id': ticket.company_id,
                'status': 'escalated' if escalated else 'solution_proposed',
            }

            result_data = {
                'success': not escalated,
                'solution': solution,
                'escalated': escalated,
                'escalation_reason': reason,
                'triage': ticket.triage_result or {},
            }

            self.automation.notify_ticket_resolution(
                ticket_data=ticket_data,
                result=result_data,
                user_email=ticket.user_email,
            )
            self.audit.log_event(ticket.id, "user_notified", {
                "email": ticket.user_email,
                "escalated": escalated,
            })

        except Exception as e:
            logger.error(f"[{ticket.id}] Notification failed: {e}")

    # ── Helpers ──────────────────────────────────────────────────

    def _update_status(self, ticket: Ticket, status: TicketStatus):
        """Update ticket status and log."""
        ticket.status = status
        logger.debug(f"[{ticket.id}] Status -> {status.value}")

    def _flatten_triage(self, triage_result: Dict) -> Dict:
        """Flatten triage result for the API response."""
        return {
            'type': triage_result.get('type', ''),
            'type_confidence': round(triage_result.get('type_confidence', 0) * 100, 1),
            'priority': triage_result.get('priority', ''),
            'priority_confidence': round(triage_result.get('priority_confidence', 0) * 100, 1),
            'queue': triage_result.get('queue', ''),
            'queue_confidence': round(triage_result.get('queue_confidence', 0) * 100, 1),
            'tags': [t['tag'] for t in triage_result.get('tags', [])[:5]],
        }


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("\n" + "=" * 60)
    print("  ECE Workflow Manager - Standalone Test")
    print("=" * 60)

    # Initialize (this will load ML models - takes ~30 seconds)
    print("\n[1/2] Initializing Workflow Manager...")
    wm = WorkflowManager()

    # Process a test ticket
    print("\n[2/2] Processing test ticket...")
    result = wm.process_ticket(
        subject="Cannot access shared drive",
        body="I'm getting 'Access Denied' error when trying to open the Marketing "
             "shared drive. I was able to access it yesterday but today it says I "
             "don't have permissions. My username is jsmith@company.com.",
        user_email="test@example.com",
        user_id="test_user",
    )

    # Print results
    print("\n" + "=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(f"  Ticket ID:    {result.ticket_id}")
    print(f"  Success:      {result.success}")
    print(f"  Escalated:    {result.escalated}")
    print(f"  Confidence:   {result.confidence:.0%}")
    print(f"  Attempts:     {result.total_attempts}")
    print(f"  Duration:     {result.duration_seconds:.1f}s")
    print(f"  Actions:      {len(result.actions_taken)}")
    for i, action in enumerate(result.actions_taken, 1):
        print(f"    {i}. {action}")
    if result.resolution:
        print(f"\n  Resolution:\n    {result.resolution[:300]}...")
    if result.escalation_reason:
        print(f"\n  Escalation Reason: {result.escalation_reason}")
    print("=" * 60)