"""
Process Monitor Agent
=====================

A resilient, self-healing background service that continuously monitors the health,
performance, and SLA compliance of the Enterprise Context Engine (ECE).

This agent:
1. Runs a background monitoring loop.
2. Collects structured metrics from the database (SQLite).
3. Reports health status to a centralized StatusReporter.
4. Detects SLA breaches and system anomalies.

Fixes applied (v2):
- FIX 1: Added __init__ log message to match codebase convention.
- FIX 2: resolved_count now uses correct ECE status value ('solution_proposed').
- FIX 3: avg_resolution_time query uses julianday() instead of strftime('%s')
          for cross-platform compatibility (Windows-safe).
- FIX 4: _check_agent_health() is now wrapped in its own try/except inside
          _collect_metrics() so an agent health failure never corrupts the
          metrics dict or crashes the collection cycle.
- FIX 5: Solver/triage health check logic restructured to avoid the shadowing
          bug where solver was never marked healthy if triage was missing.
"""

import logging
import threading
import time
import sqlite3
import importlib
from datetime import datetime
from typing import Any, Dict, Optional

# Match existing codebase logging pattern
logger = logging.getLogger(__name__)


class ProcessMonitor:
    """
    Resilient background monitor for ECE system health and metrics.

    Spawns a single daemon thread that wakes every `check_interval_seconds`
    to collect metrics from the SQLite database, check agent health, detect
    SLA breaches, and forward a structured metrics snapshot to the
    StatusReporter.

    The thread is designed to NEVER crash — all errors are caught, logged,
    and the loop continues on the next interval.
    """

    def __init__(self, db_path: str, status_reporter: Any, check_interval_seconds: int = 60):
        """
        Initialize the ProcessMonitor.

        Args:
            db_path:                  Path to the SQLite database file.
            status_reporter:          Object exposing receive_metrics(metrics: dict).
                                      Typically a StatusReporter instance.
            check_interval_seconds:   How often (in seconds) to collect metrics.
                                      Default: 60 seconds.
        """
        self.db_path = db_path
        self.status_reporter = status_reporter
        self.check_interval_seconds = check_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # FIX 1: Added init log to match every other agent in the codebase
        logger.info(
            "ProcessMonitor initialized. Interval: %ds, DB: %s",
            check_interval_seconds,
            db_path
        )

    def start(self):
        """
        Spawn and start the daemon monitoring thread.

        Guards against double-start — safe to call multiple times.
        """
        if self._thread and self._thread.is_alive():
            logger.warning("ProcessMonitor thread is already running.")
            return

        logger.info("Starting ProcessMonitor agent...")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="ProcessMonitor"
        )
        self._thread.start()
        logger.info("ProcessMonitor agent started successfully.")

    def stop(self):
        """
        Signal the monitoring loop to terminate gracefully.

        Uses threading.Event so the sleeping thread wakes immediately
        rather than waiting the full interval.
        """
        logger.info("Stopping ProcessMonitor agent...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            logger.info("ProcessMonitor agent stopped.")

    def _monitor_loop(self):
        """
        Main monitoring loop.

        Wakes up every check_interval_seconds, collects metrics, and
        pushes them to the StatusReporter. Uses _stop_event.wait() as
        the sleep mechanism so stop() wakes the thread instantly.

        The broad try/except ensures the thread NEVER dies on an error —
        it always logs and continues to the next cycle.
        """
        logger.info(
            "ProcessMonitor loop running. Checking every %s seconds.",
            self.check_interval_seconds
        )

        while not self._stop_event.is_set():
            try:
                # Record cycle start for duration logging
                start_time = time.time()

                # Collect all metrics from DB + agent health
                metrics = self._collect_metrics()

                # Forward snapshot to StatusReporter
                if self.status_reporter:
                    self.status_reporter.receive_metrics(metrics)

                # Log a one-line cycle summary
                duration = time.time() - start_time
                logger.info(
                    "Monitor cycle complete (%.2fs). Tickets: %d, Breaches: %d, DB: %s",
                    duration,
                    metrics.get('total_tickets', 0),
                    metrics.get('sla_breach_count', 0),
                    metrics.get('db_status', 'unknown')
                )

            except Exception as e:
                # Broad catch — log and continue. Thread must never crash.
                logger.error("Error in ProcessMonitor loop: %s", e, exc_info=True)

            # Smart sleep: wakes immediately if stop() is called mid-wait
            if self._stop_event.wait(self.check_interval_seconds):
                break

    def _collect_metrics(self) -> Dict[str, Any]:
        """
        Collect comprehensive system metrics from the database and agent
        health checks.

        Returns:
            A structured dict with all metric keys guaranteed to be present,
            even if their values are defaults (0, {}, 'unknown') due to errors.
        """
        # Initialise all keys with safe defaults so the dict is always complete
        metrics = {
            'timestamp':                   datetime.now().isoformat(),
            'total_tickets':               0,
            'tickets_by_status':           {},
            'tickets_by_priority':         {},
            'tickets_by_queue':            {},
            'escalation_count':            0,
            'resolved_count':              0,
            'escalation_rate_pct':         0.0,
            'avg_resolution_time_minutes': 0.0,
            'sla_breach_count':            0,
            'sla_breaches_by_priority':    {},
            'agent_health':                {},
            'db_status':                   'unknown'
        }

        conn = None
        try:
            # --- Open DB connection (match existing codebase pattern) ---
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # ----------------------------------------------------------------
            # 1. BASIC COUNTS
            # ----------------------------------------------------------------

            cursor.execute("SELECT COUNT(*) FROM classified_tickets")
            metrics['total_tickets'] = cursor.fetchone()[0]

            cursor.execute(
                "SELECT status, COUNT(*) FROM classified_tickets GROUP BY status"
            )
            metrics['tickets_by_status'] = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT pred_priority, COUNT(*) FROM classified_tickets GROUP BY pred_priority"
            )
            metrics['tickets_by_priority'] = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT pred_queue, COUNT(*) FROM classified_tickets GROUP BY pred_queue"
            )
            metrics['tickets_by_queue'] = {row[0]: row[1] for row in cursor.fetchall()}

            # ----------------------------------------------------------------
            # 2. DERIVED COUNTS
            # ----------------------------------------------------------------

            cursor.execute(
                "SELECT COUNT(*) FROM classified_tickets WHERE status = 'escalated'"
            )
            metrics['escalation_count'] = cursor.fetchone()[0]

            # FIX 2: Use the actual ECE status value from app.py.
            # The app sets status = 'solution_proposed' when a ticket is
            # resolved by the AI. 'resolved' and 'completed' do not exist
            # in the schema and would always return 0.
            cursor.execute(
                "SELECT COUNT(*) FROM classified_tickets WHERE status = 'solution_proposed'"
            )
            metrics['resolved_count'] = cursor.fetchone()[0]

            # Escalation rate as a percentage of all tickets
            if metrics['total_tickets'] > 0:
                metrics['escalation_rate_pct'] = round(
                    (metrics['escalation_count'] / metrics['total_tickets']) * 100, 1
                )

            # ----------------------------------------------------------------
            # 3. AVERAGE RESOLUTION TIME
            # ----------------------------------------------------------------
            # Join resolved tickets with their latest interaction timestamp
            # to estimate how long resolution took.
            #
            # FIX 3: Use julianday() instead of strftime('%s', ...) for
            # cross-platform safety. strftime('%s') does not work on Windows
            # SQLite builds. julianday() returns fractional days, so we
            # multiply by 1440 to convert to minutes.
            query_avg_time = """
                SELECT AVG(
                    (julianday(i.last_interaction) - julianday(t.timestamp)) * 1440.0
                )
                FROM classified_tickets t
                JOIN (
                    SELECT ticket_id, MAX(timestamp) AS last_interaction
                    FROM ticket_interactions
                    GROUP BY ticket_id
                ) i ON t.id = i.ticket_id
                WHERE t.status = 'solution_proposed'
            """
            cursor.execute(query_avg_time)
            avg_result = cursor.fetchone()[0]
            metrics['avg_resolution_time_minutes'] = round(avg_result, 1) if avg_result else 0.0

            # ----------------------------------------------------------------
            # 4. SLA BREACH DETECTION
            # ----------------------------------------------------------------
            # Fetch all non-resolved tickets and compute breach in Python.
            # A ticket is breached when:
            #   elapsed_hours > SLA_limit[priority] AND status != 'solution_proposed'
            #
            # Fetching in Python (not SQL) keeps the logic readable and testable.
            cursor.execute("""
                SELECT id, pred_priority, timestamp
                FROM classified_tickets
                WHERE status != 'solution_proposed'
            """)
            active_tickets = cursor.fetchall()

            # SLA thresholds in hours — from config.py SLA dict
            sla_limits = {
                'High':   4,
                'Medium': 24,
                'Low':    72
            }

            breach_count = 0
            breaches_by_priority = {}
            now = datetime.now()

            for ticket in active_tickets:
                # Parse the SQLite timestamp string — handle both formats
                # SQLite may store as 'YYYY-MM-DD HH:MM:SS.ffffff' or ISO 8601
                try:
                    created_at = datetime.fromisoformat(str(ticket['timestamp']))
                except ValueError:
                    try:
                        created_at = datetime.strptime(
                            str(ticket['timestamp']), '%Y-%m-%d %H:%M:%S.%f'
                        )
                    except ValueError:
                        # Skip tickets with unparseable timestamps
                        logger.warning(
                            "Skipping ticket %s — unparseable timestamp: %s",
                            ticket['id'], ticket['timestamp']
                        )
                        continue

                elapsed_hours = (now - created_at).total_seconds() / 3600
                priority = ticket['pred_priority'] or 'Low'
                # Default to Low SLA (72h) for any unknown/null priority
                limit = sla_limits.get(priority, 72)

                if elapsed_hours > limit:
                    breach_count += 1
                    breaches_by_priority[priority] = (
                        breaches_by_priority.get(priority, 0) + 1
                    )

            metrics['sla_breach_count'] = breach_count
            metrics['sla_breaches_by_priority'] = breaches_by_priority

            # Mark DB healthy — we reached the end of all queries without error
            metrics['db_status'] = 'healthy'

        except Exception as e:
            logger.error("DB Error in metrics collection: %s", e, exc_info=True)
            metrics['db_status'] = 'degraded'
        finally:
            if conn:
                conn.close()

        # ----------------------------------------------------------------
        # 5. AGENT HEALTH CHECK
        # ----------------------------------------------------------------
        # FIX 4: Wrapped in its own try/except so a failure here never
        # corrupts the metrics dict or propagates up to crash _monitor_loop.
        # This runs AFTER the finally block so the DB is already closed.
        try:
            metrics['agent_health'] = self._check_agent_health()
        except Exception as e:
            logger.error("Agent health check failed unexpectedly: %s", e, exc_info=True)
            # Return safe defaults for all agents
            metrics['agent_health'] = {
                'database':   'unknown',
                'triage':     'unknown',
                'solver':     'unknown',
                'automation': 'unknown'
            }

        return metrics

    def _check_agent_health(self) -> Dict[str, str]:
        """
        Check the runtime status of all critical ECE components.

        Returns:
            Dict mapping component names to status strings:
            'healthy' | 'unreachable' | 'unknown'
        """
        health = {
            'database':   'unknown',
            'triage':     'unknown',
            'solver':     'unknown',
            'automation': 'unknown'
        }

        # --- 1. Database ping ---
        # Lightweight SELECT 1 to verify the DB file is accessible
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            conn.close()
            health['database'] = 'healthy'
        except Exception:
            health['database'] = 'unreachable'

        # --- 2. Application agent checks ---
        # Import the Flask app module at runtime to inspect global agent objects.
        # Using importlib avoids a circular import at module load time.
        try:
            ece_app = importlib.import_module('app')

            # FIX 5: Restructured solver/triage check.
            # Old code had an if/elif that prevented solver from being marked
            # healthy when triage existed (the elif never fired if the outer
            # condition matched). Now solver and triage are checked independently.
            if hasattr(ece_app, 'solver') and ece_app.solver is not None:
                # Solver itself is alive
                health['solver'] = 'healthy'

                # Triage is a sub-component of solver — check it separately
                if (hasattr(ece_app.solver, 'triage') and
                        ece_app.solver.triage is not None):
                    health['triage'] = 'healthy'

            # Automation Specialist is an independent global object
            if (hasattr(ece_app, 'automation_specialist') and
                    ece_app.automation_specialist is not None):
                health['automation'] = 'healthy'

        except Exception as e:
            # App module not importable — leave statuses as 'unknown'
            # (not 'unreachable' — the app may simply not be in the path yet)
            logger.debug("Could not inspect app agent health: %s", e)

        return health