import sqlite3
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

# ML imports
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    pass

import config

logger = logging.getLogger(__name__)

@dataclass
class TicketCluster:
    cluster_id: str
    ticket_ids: List[str]
    ticket_subjects: List[str]
    representative_subject: str
    avg_similarity: float
    ticket_count: int
    earliest_ticket_time: str
    latest_ticket_time: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "ticket_ids": self.ticket_ids,
            "ticket_subjects": self.ticket_subjects,
            "representative_subject": self.representative_subject,
            "avg_similarity": round(self.avg_similarity * 100, 1),
            "ticket_count": self.ticket_count,
            "earliest_ticket_time": self.earliest_ticket_time,
            "latest_ticket_time": self.latest_ticket_time
        }

@dataclass
class SystemicAlert:
    alert_id: str
    cluster: TicketCluster
    severity: str
    summary: str
    recommended_action: str
    detected_at: str
    already_known: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "cluster": self.cluster.to_dict(),
            "severity": self.severity,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "detected_at": self.detected_at,
            "already_known": self.already_known
        }


class PatternMiner:
    def __init__(self, db_path: str = config.DATABASE_PATH, window_minutes: int = 60,
                 similarity_thresh: float = 0.42, cluster_threshold: int = 3,
                 max_tickets_scan: int = 200):
        self.db_path = db_path
        self.window_minutes = window_minutes
        self.similarity_thresh = similarity_thresh
        self.cluster_threshold = cluster_threshold
        self.max_tickets_scan = max_tickets_scan
        
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS systemic_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT UNIQUE,
                    cluster_id TEXT,
                    severity TEXT,
                    summary TEXT,
                    recommended_action TEXT,
                    detected_at TEXT,
                    ticket_count INTEGER,
                    avg_similarity REAL,
                    ticket_ids TEXT,
                    ticket_subjects TEXT,
                    representative_subject TEXT
                )
            ''')
            conn.commit()
            conn.close()
            logger.info("PatternMiner: systemic_alerts table verified/created.")
        except Exception as e:
            logger.error(f"PatternMiner DB init error: {e}")

    def mine(self, new_ticket_id: str, subject: str, body: str) -> Optional[SystemicAlert]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Step 1: Load recent tickets
            cutoff_time = datetime.now() - timedelta(minutes=self.window_minutes)
            
            cursor.execute('''
                SELECT id, subject, body, timestamp 
                FROM classified_tickets
                WHERE timestamp >= ?
                  AND status NOT IN ('resolved', 'closed')
                  AND id != ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (cutoff_time.isoformat(), new_ticket_id, self.max_tickets_scan))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows and self.cluster_threshold > 1:
                return None
                
            # Prepare corpus
            new_text = f"{subject} {body}"
            corpus = [new_text]
            ticket_meta = [{'id': new_ticket_id, 'subject': subject, 'timestamp': datetime.now().isoformat()}]
            
            for row_id, r_subj, r_body, r_ts in rows:
                corpus.append(f"{r_subj} {r_body}")
                ticket_meta.append({'id': row_id, 'subject': r_subj, 'timestamp': r_ts})
                
            # Step 2: TF-IDF Vectorization
            vectorizer = TfidfVectorizer(max_features=500, stop_words='english', ngram_range=(1,2))
            X = vectorizer.fit_transform(corpus)
            
            # Step 3: Cosine Similarity
            sim_matrix = cosine_similarity(X[0:1], X)[0]
            
            related_tickets = []
            similarities = []
            for i, sim in enumerate(sim_matrix):
                if float(sim) >= self.similarity_thresh:
                    related_tickets.append(ticket_meta[i])
                    similarities.append(float(sim))
                    
            # Step 4: Cluster Check
            ticket_count = len(related_tickets)
            if ticket_count < self.cluster_threshold:
                return None
                
            # Build Cluster
            sorted_t_ids = sorted([t['id'] for t in related_tickets])
            cluster_id = hashlib.md5("".join(sorted_t_ids).encode('utf-8')).hexdigest()
            
            times = [t['timestamp'] for t in related_tickets]
            avg_sim = sum(similarities) / len(similarities)
            
            cluster = TicketCluster(
                cluster_id=cluster_id,
                ticket_ids=sorted_t_ids,
                ticket_subjects=[t['subject'] for t in related_tickets],
                representative_subject=subject,
                avg_similarity=avg_sim,
                ticket_count=ticket_count,
                earliest_ticket_time=min(times),
                latest_ticket_time=max(times)
            )
            
            # Build Alert
            severity = "WATCH"
            if ticket_count >= 6:
                severity = "CRITICAL"
            elif ticket_count >= 4:
                severity = "HIGH"
                
            summary = (f"{ticket_count} tickets in the last {self.window_minutes} minutes "
                       f"share a common pattern. Representative issue: '{subject}'. "
                       f"Average similarity: {round(avg_sim * 100, 1)}%.")
            
            action = "Monitor this cluster. If it grows to 5+ tickets, auto-escalate to HIGH."
            if severity == "CRITICAL":
                action = "Escalate immediately to Engineering and create a P1 incident. Notify all affected users and freeze related deployments."
            elif severity == "HIGH":
                action = "Assign a senior engineer to investigate the root cause. Prepare a proactive user communication within 30 minutes."
                
            alert_id = "ALERT-" + cluster_id[:8].upper()
            
            alert = SystemicAlert(
                alert_id=alert_id,
                cluster=cluster,
                severity=severity,
                summary=summary,
                recommended_action=action,
                detected_at=datetime.now().isoformat()
            )
            
            # Step 5: Deduplication & Persistence
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT 1 FROM systemic_alerts WHERE cluster_id = ?", (cluster_id,))
            exists = cursor.fetchone()
            
            if exists:
                alert.already_known = True
            else:
                cursor.execute('''
                    INSERT INTO systemic_alerts 
                    (alert_id, cluster_id, severity, summary, recommended_action, detected_at, 
                     ticket_count, avg_similarity, ticket_ids, ticket_subjects, representative_subject)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    alert.alert_id, alert.cluster.cluster_id, alert.severity, alert.summary,
                    alert.recommended_action, alert.detected_at, alert.cluster.ticket_count,
                    alert.cluster.avg_similarity, json.dumps(alert.cluster.ticket_ids),
                    json.dumps(alert.cluster.ticket_subjects), alert.cluster.representative_subject
                ))
                conn.commit()
                
            conn.close()
            return alert
            
        except Exception as e:
            logger.error(f"PatternMiner execution error: {e}")
            return None

    def get_active_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT alert_id, cluster_id, severity, summary, recommended_action,
                       detected_at, ticket_count, avg_similarity, ticket_ids, 
                       ticket_subjects, representative_subject
                FROM systemic_alerts
                ORDER BY detected_at DESC
                LIMIT ?
            ''', (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            alerts = []
            for r in rows:
                alerts.append({
                    "alert_id": r[0],
                    "cluster_id": r[1],
                    "severity": r[2],
                    "summary": r[3],
                    "recommended_action": r[4],
                    "detected_at": r[5],
                    "ticket_count": r[6],
                    "avg_similarity": round(r[7] * 100, 1) if r[7] else 0.0,
                    "ticket_ids": json.loads(r[8]) if r[8] else [],
                    "ticket_subjects": json.loads(r[9]) if r[9] else [],
                    "representative_subject": r[10]
                })
            return alerts
            
        except Exception as e:
            logger.error(f"PatternMiner: get_active_alerts failed: {e}")
            return []

    def get_cluster_for_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            search_str = f'%"{ticket_id}"%'
            cursor.execute('''
                SELECT alert_id, cluster_id, severity, summary, recommended_action,
                       detected_at, ticket_count, avg_similarity, ticket_ids, 
                       ticket_subjects, representative_subject
                FROM systemic_alerts
                WHERE ticket_ids LIKE ?
                ORDER BY detected_at DESC LIMIT 1
            ''', (search_str,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "alert_id": row[0],
                    "cluster_id": row[1],
                    "severity": row[2],
                    "summary": row[3],
                    "recommended_action": row[4],
                    "detected_at": row[5],
                    "ticket_count": row[6],
                    "avg_similarity": round(row[7] * 100, 1) if row[7] else 0.0,
                    "ticket_ids": json.loads(row[8]) if row[8] else [],
                    "ticket_subjects": json.loads(row[9]) if row[9] else [],
                    "representative_subject": row[10]
                }
            return None
        except Exception as e:
            logger.error(f"PatternMiner: get_cluster_for_ticket failed: {e}")
            return None
