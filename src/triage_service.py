"""
TRIAGE SPECIALIST - INFERENCE SERVICE
======================================
Run with: uvicorn triage_service:app --reload
"""

import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
import pandas as pd
import numpy as np
import sqlite3
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================
MODEL_DIR = Path("trained_model")
DB_PATH = "data/tickets.db"
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ============================================================================
# 1. MODEL DEFINITION (Must match training script exactly)
# ============================================================================
class TicketTriageModel(nn.Module):
    def __init__(self, model_name, num_types, num_priorities, num_queues, num_tags=None, dropout=0.3):
        super(TicketTriageModel, self).__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        
        self.type_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, num_types)
        )
        self.priority_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, num_priorities)
        )
        self.queue_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(512, num_queues)
        )
        # Tag classifier for multi-label tag prediction
        if num_tags:
            self.tag_classifier = nn.Sequential(
                nn.Linear(hidden_size, 512), nn.ReLU(), nn.Dropout(dropout),
                nn.Linear(512, num_tags)
            )
        self.num_tags = num_tags
    
    def forward(self, input_ids, attention_mask):
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = self.dropout(outputs.pooler_output)
        type_out = self.type_classifier(pooled_output)
        priority_out = self.priority_classifier(pooled_output)
        queue_out = self.queue_classifier(pooled_output)
        if self.num_tags:
            tag_out = self.tag_classifier(pooled_output)
            return type_out, priority_out, queue_out, tag_out
        return type_out, priority_out, queue_out

# ============================================================================
# 2. SERVICE INITIALIZATION
# ============================================================================
app = FastAPI(title="ECE Triage Specialist API")

print("Loading model resources...")

# Load Config (contains both metadata and label encoders)
with open(MODEL_DIR / 'config.json', 'r') as f:
    config = json.load(f)

# Create metadata dict from config
metadata = {
    'model_name': config['model_name'],
    'num_types': config['num_types'],
    'num_priorities': config['num_priorities'],
    'num_queues': config['num_queues']
}

# Create encoders map from config
encoders_map = {
    'type_classes': config['type_classes'],
    'priority_classes': config['priority_classes'],
    'queue_classes': config['queue_classes']
}

# Initialize Tokenizer
tokenizer = AutoTokenizer.from_pretrained(metadata['model_name'])

# Initialize Model architecture
model = TicketTriageModel(
    model_name=metadata['model_name'],
    num_types=metadata['num_types'],
    num_priorities=metadata['num_priorities'],
    num_queues=metadata['num_queues'],
    num_tags=config.get('num_unique_tags')
).to(DEVICE)

# Load Trained Weights
checkpoint = torch.load(MODEL_DIR / 'model.pth', map_location=DEVICE)
model.load_state_dict(checkpoint['model_state_dict'])
model.eval() # Set to evaluation mode (turns off dropout)

print("[OK] Model loaded and ready for inference")

# ============================================================================
# 3. HELPER FUNCTIONS
# ============================================================================

def smart_truncate(subject, body, max_len=512):
    """Keeps the Subject, start of Body, and end of Body (logs/errors)."""
    # Reserve tokens for [CLS], [SEP], and Subject
    # Approximate token count by words for speed (1 word ≈ 1.3 tokens)
    subject_tokens = tokenizer.tokenize(subject)
    budget = max_len - len(subject_tokens) - 3 # Safety buffer
    
    body_tokens = tokenizer.tokenize(body)
    
    if len(body_tokens) <= budget:
        return f"{subject} [SEP] {body}"
    
    # Split budget: 25% for start (context), 75% for end (errors)
    head_budget = int(budget * 0.25)
    tail_budget = budget - head_budget
    
    head = tokenizer.convert_tokens_to_string(body_tokens[:head_budget])
    tail = tokenizer.convert_tokens_to_string(body_tokens[-tail_budget:])
    
    return f"{subject} [SEP] {head} ... {tail}"

def get_prediction_label(logits, classes_list):
    probs = torch.softmax(logits, dim=1)
    confidence, idx = torch.max(probs, dim=1)
    return classes_list[idx.item()], confidence.item()

def generate_solution(subject, body, ticket_type, priority, queue):
    """
    Generate a suggested solution based on ticket classification and content.
    Analyzes the ticket and provides actionable resolution steps.
    """
    subject_lower = subject.lower()
    body_lower = body.lower()
    full_text = f"{subject_lower} {body_lower}"
    
    # Solution templates based on queue
    queue_solutions = {
        "IT Support": {
            "intro": "This appears to be an IT-related issue.",
            "steps": [
                "1. Check if the issue is localized to your device or affects multiple users",
                "2. Try restarting your device/application",
                "3. Clear cache and temporary files",
                "4. Verify network connectivity",
                "5. Check for any recent system updates or changes"
            ],
            "escalation": "If the issue persists, escalate to Level 2 IT Support with diagnostic logs."
        },
        "Technical Support": {
            "intro": "This is a technical issue that requires investigation.",
            "steps": [
                "1. Reproduce the issue and document the exact steps",
                "2. Collect error messages, logs, and screenshots",
                "3. Check the knowledge base for similar issues",
                "4. Verify software versions and compatibility",
                "5. Test in a different environment if possible"
            ],
            "escalation": "Escalate to the engineering team if root cause cannot be identified."
        },
        "Customer Service": {
            "intro": "This is a customer service inquiry.",
            "steps": [
                "1. Review the customer's account history and previous interactions",
                "2. Understand the customer's concern fully before responding",
                "3. Provide clear and empathetic communication",
                "4. Offer available solutions or alternatives",
                "5. Follow up to ensure customer satisfaction"
            ],
            "escalation": "Escalate to a supervisor if the customer requires special consideration."
        },
        "Billing and Payments": {
            "intro": "This is a billing or payment-related issue.",
            "steps": [
                "1. Review the customer's billing history and invoices",
                "2. Verify payment methods and transaction records",
                "3. Check for any pending charges or refunds",
                "4. Explain billing policies clearly to the customer",
                "5. Process adjustments or refunds if warranted"
            ],
            "escalation": "Escalate to the finance team for complex billing disputes."
        },
        "Product Support": {
            "intro": "This is a product-related support request.",
            "steps": [
                "1. Identify the specific product and version affected",
                "2. Review product documentation and FAQs",
                "3. Check for known issues or recent product updates",
                "4. Guide the customer through troubleshooting steps",
                "5. Document the issue for the product team if it's a new bug"
            ],
            "escalation": "Escalate to the product team for feature requests or critical bugs."
        },
        "Human Resources": {
            "intro": "This is an HR-related inquiry.",
            "steps": [
                "1. Review relevant HR policies and procedures",
                "2. Gather all necessary information from the employee",
                "3. Maintain confidentiality throughout the process",
                "4. Provide clear guidance on next steps",
                "5. Document the interaction appropriately"
            ],
            "escalation": "Escalate to HR management for sensitive or complex matters."
        },
        "Sales and Pre-Sales": {
            "intro": "This is a sales or pre-sales inquiry.",
            "steps": [
                "1. Understand the customer's needs and requirements",
                "2. Present relevant products or services",
                "3. Provide pricing and availability information",
                "4. Address any concerns or objections",
                "5. Schedule follow-up or demo if needed"
            ],
            "escalation": "Escalate to sales management for large deals or special pricing."
        },
        "Returns and Exchanges": {
            "intro": "This is a return or exchange request.",
            "steps": [
                "1. Verify the order details and purchase date",
                "2. Check return/exchange eligibility based on policy",
                "3. Guide the customer through the return process",
                "4. Issue return label or exchange authorization",
                "5. Process refund or replacement as applicable"
            ],
            "escalation": "Escalate to a supervisor for exceptions to return policy."
        },
        "Service Outages and Maintenance": {
            "intro": "This relates to a service outage or maintenance.",
            "steps": [
                "1. Check the status page for known outages",
                "2. Identify affected services and estimated impact",
                "3. Communicate status updates to affected users",
                "4. Coordinate with technical teams for resolution",
                "5. Provide workarounds if available"
            ],
            "escalation": "Escalate to the incident response team for critical outages."
        },
        "General Inquiry": {
            "intro": "This is a general inquiry.",
            "steps": [
                "1. Understand the customer's question or request",
                "2. Search the knowledge base for relevant information",
                "3. Provide clear and accurate information",
                "4. Direct to the appropriate department if needed",
                "5. Follow up if additional assistance is required"
            ],
            "escalation": "Route to the appropriate specialized team if needed."
        }
    }
    
    # Priority-based urgency modifiers
    priority_modifiers = {
        "High": {
            "prefix": "HIGH PRIORITY - Immediate attention required.",
            "sla": "Target resolution: 4 hours"
        },
        "Medium": {
            "prefix": "Standard priority ticket.",
            "sla": "Target resolution: 24 hours"
        },
        "Low": {
            "prefix": "Low priority - can be addressed during normal workflow.",
            "sla": "Target resolution: 72 hours"
        }
    }
    
    # Type-based context
    type_context = {
        "Incident": "This is an unplanned interruption that needs to be resolved to restore normal service.",
        "Request": "This is a service request from a user that should be fulfilled.",
        "Problem": "This is a root cause investigation for one or more incidents.",
        "Change": "This is a change request that needs to be evaluated and implemented."
    }
    
    # Keyword-based specific solutions
    specific_solutions = []
    
    if any(word in full_text for word in ['login', 'password', 'sign in', 'access denied', 'locked out']):
        specific_solutions.append("Login/Access Issue: Verify credentials, check account status, and reset password if needed.")
    
    if any(word in full_text for word in ['slow', 'performance', 'lag', 'timeout', 'loading']):
        specific_solutions.append("Performance Issue: Check system resources, network latency, and clear browser cache.")
    
    if any(word in full_text for word in ['error', 'bug', 'crash', 'not working', 'failed']):
        specific_solutions.append("Error/Bug: Collect error logs, document reproduction steps, and check for recent changes.")
    
    if any(word in full_text for word in ['payment', 'charge', 'refund', 'invoice', 'bill']):
        specific_solutions.append("Payment Issue: Review transaction history, verify payment method, and check for processing delays.")
    
    if any(word in full_text for word in ['install', 'setup', 'configure', 'update', 'upgrade']):
        specific_solutions.append("Installation/Setup: Follow documentation, check system requirements, and verify compatibility.")
    
    # Build the solution
    queue_info = queue_solutions.get(queue, queue_solutions["General Inquiry"])
    priority_info = priority_modifiers.get(priority, priority_modifiers["Medium"])
    type_info = type_context.get(ticket_type, "")
    
    solution = {
        "summary": f"{priority_info['prefix']} {queue_info['intro']}",
        "type_context": type_info,
        "sla": priority_info['sla'],
        "recommended_steps": queue_info['steps'],
        "specific_insights": specific_solutions if specific_solutions else ["No specific patterns detected - follow standard procedures."],
        "escalation_path": queue_info['escalation']
    }
    
    return solution

# ============================================================================
# 4. API ENDPOINTS
# ============================================================================

class TicketRequest(BaseModel):
    subject: str
    body: str

class FeedbackRequest(BaseModel):
    ticket_id: int
    correct_type: str = None
    correct_priority: str = None
    correct_queue: str = None

@app.post("/classify")
async def classify_ticket(ticket: TicketRequest):
    """
    Main endpoint: Receives ticket -> Returns Classification & Confidence
    """
    # 1. Preprocess
    full_text = smart_truncate(ticket.subject, ticket.body)
    inputs = tokenizer(
        full_text, return_tensors="pt", truncation=True, 
        padding='max_length', max_length=512
    ).to(DEVICE)
    
    # 2. Inference
    with torch.no_grad():
        outputs = model(inputs['input_ids'], inputs['attention_mask'])
        # Model returns 4 outputs if tags are enabled, otherwise 3
        if len(outputs) == 4:
            type_logits, priority_logits, queue_logits, tag_logits = outputs
        else:
            type_logits, priority_logits, queue_logits = outputs
    
    # 3. Decode
    pred_type, conf_type = get_prediction_label(type_logits, encoders_map['type_classes'])
    pred_prio, conf_prio = get_prediction_label(priority_logits, encoders_map['priority_classes'])
    pred_queue, conf_queue = get_prediction_label(queue_logits, encoders_map['queue_classes'])
    
    # 4. Log to Database (Audit Trail)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS classified_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT, body TEXT,
            pred_type TEXT, conf_type REAL,
            pred_priority TEXT, conf_priority REAL,
            pred_queue TEXT, conf_queue REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            corrected BOOLEAN DEFAULT 0
        )
    ''')
    cursor.execute('''
        INSERT INTO classified_tickets 
        (subject, body, pred_type, conf_type, pred_priority, conf_priority, pred_queue, conf_queue)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (ticket.subject, ticket.body, pred_type, conf_type, pred_priority, conf_prio, pred_queue, conf_queue))
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # 5. Determine Action
    action = "ROUTINE"
    if pred_prio in ['High', 'Critical'] or conf_prio > 0.9:
        action = "URGENT_REVIEW"
    if conf_queue < 0.70:
        action = "HUMAN_TRIAGE_REQUIRED"

    return {
        "ticket_id": ticket_id,
        "classification": {
            "type": pred_type,
            "priority": pred_prio,
            "queue": pred_queue
        },
        "confidence": {
            "type": round(conf_type, 4),
            "priority": round(conf_prio, 4),
            "queue": round(conf_queue, 4)
        },
        "recommended_action": action
    }

@app.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """
    Feedback Loop: Human agents correct the model here.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Fetch original ticket text
    cursor.execute("SELECT subject, body FROM classified_tickets WHERE id = ?", (feedback.ticket_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket ID not found")
    
    subject, body = row
    
    # 2. Add to Learning Buffer (The "Experience Replay" Memory)
    # Only add fields that were corrected (if provided), otherwise keep original prediction?
    # Actually, for training, we need ALL labels. 
    # In a real app, you'd fetch the originals and overwrite with corrections.
    # For simplicity, we assume the frontend sends the full correct set.
    
    if feedback.correct_type and feedback.correct_priority and feedback.correct_queue:
        cursor.execute('''
            INSERT INTO learning_buffer (subject, body, type, priority, queue)
            VALUES (?, ?, ?, ?, ?)
        ''', (subject, body, feedback.correct_type, feedback.correct_priority, feedback.correct_queue))
        
        # Mark as corrected in audit log
        cursor.execute("UPDATE classified_tickets SET corrected = 1 WHERE id = ?", (feedback.ticket_id,))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Model memory updated. Will apply at next training cycle."}
    
    conn.close()
    return {"status": "ignored", "message": "Incomplete labels provided."}