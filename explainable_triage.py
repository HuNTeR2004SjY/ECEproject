import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any
import traceback

import config

logger = logging.getLogger(__name__)

@dataclass
class TriageExplanation:
    # Decision fields
    type: str = "Unknown"
    priority: str = "Unknown"
    queue: str = "Unknown"
    
    # Confidence percentages
    type_confidence_pct: float = 0.0
    priority_confidence_pct: float = 0.0
    queue_confidence_pct: float = 0.0
    
    # Tier fields
    type_tier: str = "LOW"
    priority_tier: str = "LOW"
    queue_tier: str = "LOW"
    
    # Rationale fields
    type_reason: str = ""
    priority_reason: str = ""
    queue_reason: str = ""
    
    # Evidence fields
    evidence_signals: List[str] = field(default_factory=list)
    
    # Review fields
    needs_human_review: bool = True
    review_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "priority": self.priority,
            "queue": self.queue,
            "type_confidence_pct": round(self.type_confidence_pct, 1),
            "priority_confidence_pct": round(self.priority_confidence_pct, 1),
            "queue_confidence_pct": round(self.queue_confidence_pct, 1),
            "type_tier": self.type_tier,
            "priority_tier": self.priority_tier,
            "queue_tier": self.queue_tier,
            "type_reason": self.type_reason,
            "priority_reason": self.priority_reason,
            "queue_reason": self.queue_reason,
            "evidence_signals": self.evidence_signals,
            "needs_human_review": self.needs_human_review,
            "review_reason": self.review_reason
        }


class ExplainableTriageWrapper:
    def __init__(self):
        # Common urgency words to detect
        self.urgency_words = [
            "urgent", "critical", "down", "failed", "broken", 
            "emergency", "asap", "immediate", "offline", "fatal",
            "outage", "sev1", "p1", "blocked"
        ]

    def _get_tier(self, confidence: float) -> str:
        if confidence >= 0.80:
            return "HIGH"
        elif confidence >= 0.55:
            return "MEDIUM"
        return "LOW"

    def _extract_evidence(self, triage_result: dict, combined_text: str) -> List[str]:
        evidence = []
        text_lower = combined_text.lower()
        
        # 1. Check for queue keywords defined in config
        if hasattr(config, 'QUEUE_KEYWORDS'):
            for queue_name, keywords in config.QUEUE_KEYWORDS.items():
                for kw in keywords:
                    if kw.lower() in text_lower:
                        evidence.append(f'Keyword match: "{kw}"')
                        if len(evidence) >= 6:
                            return evidence

        # 2. Add predicted tags
        tags = triage_result.get('tags', [])
        for tag_obj in tags:
            tag_name = tag_obj.get('tag', '')
            if tag_name:
                evidence.append(f'Predicted tag: {tag_name}')
                if len(evidence) >= 6:
                    return evidence

        # 3. Check for urgency words
        for word in self.urgency_words:
            # simple word boundary check
            if f" {word} " in f" {text_lower} ":
                evidence.append(f'Urgency indicator: "{word}"')
                if len(evidence) >= 6:
                    return evidence
                    
        return evidence[:6]

    def explain(self, triage_result: dict, ticket_subject: str, ticket_body: str) -> TriageExplanation:
        """
        Generates a human-readable justification for the AI's triage decisions.
        Runs safely; returns a fallback explanation if it crashes.
        """
        try:
            exp = TriageExplanation()
            
            # 1. Parse decisions & raw confidences
            exp.type = triage_result.get('type', 'Unknown')
            exp.priority = triage_result.get('priority', 'Unknown')
            exp.queue = triage_result.get('queue', 'Unknown')
            
            raw_type_conf = float(triage_result.get('type_confidence', 0.0))
            raw_pri_conf = float(triage_result.get('priority_confidence', 0.0))
            raw_q_conf = float(triage_result.get('queue_confidence', 0.0))
            
            # 2. Set percentages
            exp.type_confidence_pct = raw_type_conf * 100
            exp.priority_confidence_pct = raw_pri_conf * 100
            exp.queue_confidence_pct = raw_q_conf * 100
            
            # 3. Set tiers
            exp.type_tier = self._get_tier(raw_type_conf)
            exp.priority_tier = self._get_tier(raw_pri_conf)
            exp.queue_tier = self._get_tier(raw_q_conf)

            # 4. Generate Rationale
            exp.type_reason = f"Classified as {exp.type} based on contextual mapping (Confidence: {exp.type_tier})."
            exp.priority_reason = f"Priority marked as {exp.priority} due to perceived impact (Confidence: {exp.priority_tier})."
            exp.queue_reason = f"Routed to the {exp.queue} queue based on issue domain (Confidence: {exp.queue_tier})."
            
            # 5. Extract Evidence
            combined_text = f"{ticket_subject} {ticket_body}"
            exp.evidence_signals = self._extract_evidence(triage_result, combined_text)
            
            if not exp.evidence_signals:
                exp.evidence_signals.append("No explicit keyword or tag evidence found; relies on general semantic similarity.")
            
            # 6. Review Logic
            low_fields = []
            if exp.type_tier == "LOW": low_fields.append("Type")
            if exp.priority_tier == "LOW": low_fields.append("Priority")
            if exp.queue_tier == "LOW": low_fields.append("Queue")
            
            if low_fields:
                exp.needs_human_review = True
                exp.review_reason = f"Low confidence detected in: {', '.join(low_fields)}"
            else:
                exp.needs_human_review = False
                exp.review_reason = "All predictions meet acceptable confidence thresholds."
                
            return exp
            
        except Exception as e:
            logger.error(f"Error generating Explainable AI triage: {e}")
            logger.error(traceback.format_exc())
            # Fallback safe return
            return TriageExplanation(
                needs_human_review=True,
                review_reason="Error occurred during explanation generation. System fell back to failsafe."
            )
