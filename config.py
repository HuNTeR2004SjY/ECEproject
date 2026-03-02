"""
ECE System Configuration
========================

Centralized configuration for all ECE components.
All thresholds, paths, model settings, and other configurations
should be defined here instead of being hardcoded across files.
"""

import os
from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================

# Project root (where this config file is located)
PROJECT_DIR = Path(__file__).parent.resolve()

# Database path
DATABASE_PATH = os.getenv('ECE_DATABASE_PATH', str(PROJECT_DIR / 'data' / 'tickets.db'))

# Model directory
MODEL_DIR = os.getenv('ECE_MODEL_DIR', str(PROJECT_DIR / 'trained_model'))

# Data file for knowledge base
DATA_FILE = os.getenv('ECE_DATA_FILE', str(PROJECT_DIR / 'data' / 'processed_tickets.csv'))

# ============================================================================
# SERVER SETTINGS
# ============================================================================

SERVER = {
    'host': os.getenv('ECE_HOST', '0.0.0.0'),
    'port': int(os.getenv('ECE_PORT', 5000)),
    'debug': os.getenv('ECE_DEBUG', 'false').lower() == 'true',
}

# ============================================================================
# MODEL CONFIGURATION
# ============================================================================

# Generator model for solution generation
# Options: google/flan-t5-base, google/flan-t5-large, google/flan-t5-xl
# Using 'large' for better reasoning capabilities
GENERATOR_MODEL = os.getenv('ECE_GENERATOR_MODEL', 'google/flan-t5-large')

# Generation parameters (optimized for flan-t5-large)
GENERATION = {
    'max_input_length': 1024,
    'max_output_length': 400,
    'min_output_length': 60,
    'num_beams': 3,               # Improved quality
    'do_sample': False,
    'temperature': 0.7,
    'no_repeat_ngram_size': 3,    # Prevent repetition loops
}

# Tokenizer max length
TOKENIZER_MAX_LENGTH = 512

# ============================================================================
# PROBLEM SOLVER THRESHOLDS
# ============================================================================

SOLVER = {
    # Similarity threshold for using retrieved answer directly (no generation)
    # Set HIGH to ensure Problem Solver handles most tickets
    'direct_retrieval_threshold': 0.95,
    
    # Similarity threshold below which to trigger web search
    'web_search_trigger': 0.80,   # Increased to use web search more often
    
    # Maximum retry attempts before escalation
    'max_attempts': 3,
    
    # Whether web search is enabled
    'enable_web_search': True,    # ENABLED for "proper" solutions
}

# ============================================================================
# VALIDATION THRESHOLDS
# ============================================================================

VALIDATION = {
    # Minimum solution length in characters
    'min_solution_length': 50,
    
    # Maximum overlap ratio between question and answer (prevents repetition)
    'max_question_overlap': 0.80,
    
    # Minimum confidence for queue routing before flagging for human review
    'low_confidence_queue_threshold': 0.70,
    
    # Tag prediction threshold
    'tag_threshold': 0.3,
    
    # Top K tags to return
    'top_k_tags': 5,
}

# ============================================================================
# QUALITY GATEKEEPER THRESHOLDS
# ============================================================================

QUALITY = {
    # Minimum accuracy threshold
    'min_accuracy': 0.70,
    
    # Minimum dataset size
    'min_data_size': 1000,
    
    # Maximum allowed missing data ratio
    'max_missing_data': 0.05,
    
    # Minimum confidence threshold
    'min_confidence': 0.60,
    
    # Minimum samples per class
    'min_class_samples': 50,
    
    # Text length limits
    'min_text_length': 10,
    'max_text_length': 10000,
    
    # Model size limits (in MB)
    'min_model_size_mb': 0.1,
    'max_model_size_mb': 5000,
    
    # Approval thresholds (percentage)
    'approval_threshold': 80,
    'warning_threshold': 60,
}

# ============================================================================
# REQUIRED FILES FOR VALIDATION
# ============================================================================

REQUIRED_FILES = [
    'preprocess_4tags.py',
    'train_model.py', 
    'inference_service_full.py',
    'problem_solver_fixed.py',
]

OPTIONAL_FILES = [
    'README.md',
    'requirements.txt',
    'config.json',
    '.gitignore',
]

MODEL_DIRS = [
    'trained_model',
    'model_output', 
    'models',
]

DATA_COLUMNS = {
    'required': ['subject', 'body'],
    'target': ['priority', 'type'],
}

# ============================================================================
# SCORING WEIGHTS FOR QUALITY GATEKEEPER
# ============================================================================

SCORING_WEIGHTS = {
    'project_structure': 20,
    'code_quality': 20,
    'data_quality': 30,
    'model_performance': 20,
    'documentation': 10,
}

# ============================================================================
# SLA TARGETS (in hours)
# ============================================================================

SLA = {
    'High': 4,
    'Medium': 24,
    'Low': 72,
}

# ============================================================================
# SOLUTION PROMPT TEMPLATE
# ============================================================================

SOLUTION_PROMPT_TEMPLATE = """Background Context:
{retrieved_answer}
{web_context}

Ticket Subject: {subject}
Ticket Description: {body}

{history}

Task: You are an expert technical support agent. Provide a direct, actionable solution to resolve this issue. 
Your response MUST be a clear, step-by-step procedure.
- Use imperative verbs (e.g. 'Go to', 'Click', 'Run').
- Focus on fixing the specific problem described.
- Do not provide general background information.

{previous_feedback}

Solution Steps:"""

# ============================================================================
# VALIDATION KEYWORDS
# ============================================================================

ACTION_INDICATORS = [
    'step', 'click', 'go to', 'open', 'select', 'try',
    'please', 'can', 'should', 'will', 'follow', 'check',
    'ensure', 'navigate', 'enter', 'submit', 'verify'
]

FILLER_PHRASES = [
    "i apologize for any inconvenience",
    "thank you for contacting",
    "we are here to help",
    "we appreciate your patience",
]

QUEUE_KEYWORDS = {
    'Technical Support': ['system', 'error', 'software', 'bug', 'technical', 'crash', 'debug'],
    'IT Support': ['access', 'account', 'password', 'network', 'vpn', 'login', 'permission'],
    'Billing and Payments': ['invoice', 'payment', 'charge', 'refund', 'billing', 'subscription'],
    'Product Support': ['product', 'feature', 'functionality', 'how to', 'guide', 'tutorial'],
    'Human Resources': ['leave', 'payroll', 'benefits', 'policy', 'hr'],
    'Customer Service': ['complaint', 'feedback', 'experience', 'satisfaction'],
}

# ============================================================================
# AUTOMATION SPECIALIST CONFIGURATION
# ============================================================================

# Email Configuration
EMAIL_CONFIG = {
    'enabled': os.getenv('ECE_EMAIL_ENABLED', 'true').lower() == 'true',
    'smtp_host': os.getenv('ECE_SMTP_HOST', 'smtp.gmail.com'),
    'smtp_port': int(os.getenv('ECE_SMTP_PORT', 587)),
    'smtp_user': os.getenv('ECE_SMTP_USER', 'eceproject2026@gmail.com'),
    'smtp_password': os.getenv('ECE_SMTP_PASSWORD', 'jzox vdvu dmwg avjr'),
    'from_email': os.getenv('ECE_FROM_EMAIL', 'eceproject2026@gmail.com'),
}

# GenAI Configuration
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyDuv7wFye96-k7wcuAG7cg_xHZcSv0E3Wo')
GENAI_EMAIL_MODEL = os.getenv('ECE_GENAI_EMAIL_MODEL', 'gemini-pro')

# Groq Configuration
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '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')
GROQ_EMAIL_MODEL = os.getenv('ECE_GROQ_EMAIL_MODEL', 'llama-3.3-70b-versatile')

# Application URLs
APP_URL = os.getenv('ECE_APP_URL', 'http://localhost:5000')
SUPPORT_EMAIL = os.getenv('ECE_SUPPORT_EMAIL', 'eceproject2026+support@gmail.com')

# Notification Preferences
NOTIFICATION_DEFAULTS = {
    'channels': {
        'popup': True,
        'email': True,
        'sms': False
    },
    'events': {
        'ticket_created': {'email': True, 'popup': False},
        'solution_ready': {'email': True, 'popup': True},
        'human_escalation': {'email': True, 'popup': True},
        'ticket_resolved': {'email': True, 'popup': True}
    }
}

# Escalation SLA (minutes)
ESCALATION_SLA = {
    'IT': 30,
    'HR': 60,
    'FACILITIES': 120,
    'ENGINEERING': 15
}

# ── Jira Integration ────────────────────────────────────────────────────
JIRA = {
    'base_url':    os.getenv('JIRA_BASE_URL',    'https://eceproject2026.atlassian.net'),
    'email':       os.getenv('JIRA_EMAIL',       'eceproject2026@gmail.com'),
    'api_token':   os.getenv('JIRA_API_TOKEN',   'ATATT3xFfGF0Jel98_G9j-Ol7K-XXmYrhgracskDm-KoKYPJye5phlKWndkiWuZm2etsup0Qau9IA1WmURYMGGo8_3wksPzQWnFIMSzbs0_8R0JKZcZMODdZlviR9lVRpApnthAel0-43BWSdTxLr2S9TUipUOagMvaMKPVb-4y-SiBWNk1p6EU=8160D9AE'),
    'project_key': os.getenv('JIRA_PROJECT_KEY', 'ECE'),
    'enabled':     os.getenv('JIRA_ENABLED',     'true').lower() == 'true',
}

# Jira issue type map — ECE type → Jira issue type name
JIRA_TYPE_MAP = {
    'Incident':        'Bug',
    'Problem':         'Bug',
    'Change Request':  'Task',
    'Service Request': 'Task',
    'Feature Request': 'Story',
    'Question':        'Task',
    'Complaint':       'Task',
}

# Jira priority map — ECE priority → Jira priority name
JIRA_PRIORITY_MAP = {
    'High':   'High',
    'Medium': 'Medium',
    'Low':    'Low',
}
