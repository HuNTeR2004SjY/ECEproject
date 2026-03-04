"""
TRIAGE SPECIALIST - INFERENCE SERVICE
======================================
Provides the TriageSpecialist class for ticket classification.
Loads model from trained_model directory.
"""

import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
import numpy as np
import sqlite3
import json
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from sklearn.metrics.pairwise import cosine_similarity

# Import centralized configuration
import config

# ============================================================================
# CONFIGURATION (using centralized config)
# ============================================================================
MODEL_DIR = Path(config.MODEL_DIR)
DB_PATH = config.DATABASE_PATH

# ============================================================================
# MODEL DEFINITION (Must match training script exactly)
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
    
    def get_embeddings(self, input_ids, attention_mask):
        """Get embeddings for similarity search."""
        with torch.no_grad():
            outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
            return outputs.pooler_output


# ============================================================================
# TRIAGE SPECIALIST CLASS
# ============================================================================
class TriageSpecialist:
    """
    Triage Specialist for ticket classification with answer retrieval.
    
    This class provides:
    1. Multi-task classification (type, priority, queue, tags)
    2. Knowledge base retrieval for similar past solutions
    3. Confidence scores for all predictions
    """
    
    def __init__(self, 
                 model_dir: str = None,
                 db_path: str = None):
        """
        Initialize the Triage Specialist.
        
        Args:
            model_dir: Path to directory containing model files (uses config default)
            db_path: Path to SQLite database with tickets (uses config default)
        """
        model_dir = model_dir or config.MODEL_DIR
        db_path = db_path or config.DATABASE_PATH
        
        self.model_dir = Path(model_dir)
        self.db_path = db_path
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        print(f"[init] Loading Triage Specialist on {self.device}...")
        
        # Load config
        with open(self.model_dir / 'config.json', 'r') as f:
            self.config = json.load(f)
        
        # Extract metadata
        self.model_name = self.config['model_name']
        self.type_classes = self.config['type_classes']
        self.priority_classes = self.config['priority_classes']
        self.queue_classes = self.config['queue_classes']
        self.tag_classes = self.config.get('tag_classes', [])
        
        # Load tokenizer
        print(f"  Loading tokenizer ({self.model_name})...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        
        # Initialize model
        print(f"  Loading model architecture...")
        self.model = TicketTriageModel(
            model_name=self.model_name,
            num_types=self.config['num_types'],
            num_priorities=self.config['num_priorities'],
            num_queues=self.config['num_queues'],
            num_tags=self.config.get('num_unique_tags')
        ).to(self.device)
        
        # Load trained weights
        print(f"  Loading trained weights...")
        try:
            # Use mmap=True to save memory
            checkpoint = torch.load(self.model_dir / 'model.pth', map_location=self.device, mmap=True, weights_only=False)
        except TypeError:
            # Fallback for older torch versions
            checkpoint = torch.load(self.model_dir / 'model.pth', map_location=self.device, weights_only=False)
            
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        # Load tag binarizer if available
        tag_binarizer_path = self.model_dir / 'tag_binarizer.pkl'
        if tag_binarizer_path.exists():
            with open(tag_binarizer_path, 'rb') as f:
                self.tag_binarizer = pickle.load(f)
        else:
            self.tag_binarizer = None
        
        # Load knowledge base embeddings (if available)
        self._load_knowledge_base()
        
        print("[done] Triage Specialist ready")
    
    def _load_knowledge_base(self):
        """Load or create knowledge base embeddings for retrieval."""
        self.kb_embeddings = None
        self.kb_answers = []
        self.kb_subjects = []
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if we have a tickets table with answers
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name IN ('tickets', 'classified_tickets', 'learning_buffer')
            """)
            tables = [row[0] for row in cursor.fetchall()]
            
            if 'learning_buffer' in tables:
                cursor.execute("SELECT subject, body, answer FROM learning_buffer WHERE answer IS NOT NULL LIMIT 1000")
                rows = cursor.fetchall()
                if rows:
                    self.kb_subjects = [row[0] for row in rows]
                    self.kb_answers = [row[2] for row in rows]
                    print(f"  Loaded {len(self.kb_answers)} answers from knowledge base")
            
            # Load from CSV if we have few examples
            # Only use Mode Processed Ticket if learning buffer is empty/small
            if len(self.kb_answers) < 100:
                import pandas as pd
                try:
                    data_path = config.DATA_FILE
                    if Path(data_path).exists():
                        # Read limited rows to save memory (SEVERE OOM protection)
                        # If this fails with MemoryError, we catch it below
                        df = pd.read_csv(data_path, nrows=100) 
                        if 'answer' in df.columns:
                            # Filter for valid answers
                            valid_df = df[df['answer'].notna() & (df['answer'] != '')].head(50)
                            self.kb_subjects.extend(valid_df['subject'].tolist())
                            self.kb_answers.extend(valid_df['answer'].tolist())
                            print(f"  Loaded {len(valid_df)} answers from processed tickets CSV")
                except (MemoryError, OSError) as mem_err:
                     print(f"  [warn] System out of memory while loading KB: {mem_err}. Continuing with empty KB.")
                except Exception as ex:
                    print(f"  [warn] Could not load CSV data: {ex}")
            
            conn.close()
        except Exception as e:
            print(f"  [warn] Could not load knowledge base: {e}")
    
    def _smart_truncate(self, subject: str, body: str, max_len: int = 512) -> str:
        """Truncate text while preserving important parts."""
        subject_tokens = self.tokenizer.tokenize(subject)
        budget = max_len - len(subject_tokens) - 3
        
        body_tokens = self.tokenizer.tokenize(body)
        
        if len(body_tokens) <= budget:
            return f"{subject} [SEP] {body}"
        
        # Split budget: 25% for start, 75% for end (errors usually at end)
        head_budget = int(budget * 0.25)
        tail_budget = budget - head_budget
        
        head = self.tokenizer.convert_tokens_to_string(body_tokens[:head_budget])
        tail = self.tokenizer.convert_tokens_to_string(body_tokens[-tail_budget:])
        
        return f"{subject} [SEP] {head} ... {tail}"
    
    def _get_prediction(self, logits: torch.Tensor, classes: List[str]) -> Tuple[str, float]:
        """Get prediction and confidence from logits."""
        probs = torch.softmax(logits, dim=1)
        confidence, idx = torch.max(probs, dim=1)
        return classes[idx.item()], confidence.item()
    
    def _get_tags(self, tag_logits: torch.Tensor, threshold: float = None, top_k: int = None) -> List[Dict]:
        """Get top tags with confidence scores."""
        threshold = threshold if threshold is not None else config.VALIDATION['tag_threshold']
        top_k = top_k if top_k is not None else config.VALIDATION['top_k_tags']
        
        probs = torch.sigmoid(tag_logits).squeeze()
        
        # Get indices above threshold or top_k, whichever is more
        indices = torch.where(probs > threshold)[0]
        if len(indices) < top_k:
            _, indices = torch.topk(probs, min(top_k, len(probs)))
        
        tags = []
        for idx in indices[:top_k]:
            idx = idx.item()
            if idx < len(self.tag_classes):
                tags.append({
                    'tag': self.tag_classes[idx],
                    'confidence': probs[idx].item()
                })
        
        return sorted(tags, key=lambda x: x['confidence'], reverse=True)
    
    def _retrieve_answer(self, subject: str, body: str) -> Dict:
        """Retrieve most similar answer from knowledge base."""
        if not self.kb_answers:
            return {'answer': None, 'similarity': 0.0, 'source_subject': None}
        
        # Get embedding for query
        full_text = self._smart_truncate(subject, body)
        inputs = self.tokenizer(
            full_text, return_tensors="pt", truncation=True,
            padding='max_length', max_length=config.TOKENIZER_MAX_LENGTH
        ).to(self.device)
        
        query_embedding = self.model.get_embeddings(
            inputs['input_ids'], inputs['attention_mask']
        ).cpu().numpy()
        
        # Compare with knowledge base (simple approach - could use FAISS for scale)
        best_similarity = 0.0
        best_answer = None
        best_subject = None
        
        for i, kb_subject in enumerate(self.kb_subjects):
            kb_text = self._smart_truncate(kb_subject, "")
            kb_inputs = self.tokenizer(
                kb_text, return_tensors="pt", truncation=True,
                padding='max_length', max_length=config.TOKENIZER_MAX_LENGTH
            ).to(self.device)
            
            kb_embedding = self.model.get_embeddings(
                kb_inputs['input_ids'], kb_inputs['attention_mask']
            ).cpu().numpy()
            
            similarity = cosine_similarity(query_embedding, kb_embedding)[0][0]
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_answer = self.kb_answers[i]
                best_subject = kb_subject
        
        return {
            'answer': best_answer,
            'similarity': float(best_similarity),
            'source_subject': best_subject
        }
    
    def predict(self, 
                subject: str, 
                body: str,
                retrieve_answer: bool = False) -> Dict:
        """
        Classify a ticket and optionally retrieve a similar answer.
        
        Args:
            subject: Ticket subject line
            body: Ticket body/description
            retrieve_answer: Whether to search knowledge base for similar solutions
            
        Returns:
            Dictionary with predictions, confidences, and optionally retrieved answer
        """
        # Prepare input
        full_text = self._smart_truncate(subject, body)
        inputs = self.tokenizer(
            full_text, return_tensors="pt", truncation=True,
            padding='max_length', max_length=config.TOKENIZER_MAX_LENGTH
        ).to(self.device)
        
        # Run inference
        with torch.no_grad():
            outputs = self.model(inputs['input_ids'], inputs['attention_mask'])
        
        # Parse outputs
        if len(outputs) == 4:
            type_logits, priority_logits, queue_logits, tag_logits = outputs
            tags = self._get_tags(tag_logits)
        else:
            type_logits, priority_logits, queue_logits = outputs
            tags = []
        
        # Get predictions
        pred_type, conf_type = self._get_prediction(type_logits, self.type_classes)
        pred_priority, conf_priority = self._get_prediction(priority_logits, self.priority_classes)
        pred_queue, conf_queue = self._get_prediction(queue_logits, self.queue_classes)
        
        result = {
            'type': pred_type,
            'type_confidence': conf_type,
            'priority': pred_priority,
            'priority_confidence': conf_priority,
            'queue': pred_queue,
            'queue_confidence': conf_queue,
            'tags': tags
        }
        
        # Retrieve answer if requested
        if retrieve_answer:
            retrieval = self._retrieve_answer(subject, body)
            result['answer'] = retrieval['answer'] or ""
            result['answer_source'] = {
                'similarity': retrieval['similarity'],
                'source_subject': retrieval['source_subject']
            }
        
        return result


# ============================================================================
# STANDALONE USAGE
# ============================================================================
if __name__ == "__main__":
    # Quick test
    specialist = TriageSpecialist()
    
    result = specialist.predict(
        subject="Cannot access shared drive",
        body="I'm getting Access Denied error when trying to open the Marketing shared drive.",
        retrieve_answer=True
    )
    
    print("\n[info] Classification Result:")
    print(f"  Type: {result['type']} ({result['type_confidence']:.1%})")
    print(f"  Priority: {result['priority']} ({result['priority_confidence']:.1%})")
    print(f"  Queue: {result['queue']} ({result['queue_confidence']:.1%})")
    print(f"  Tags: {[t['tag'] for t in result['tags'][:3]]}")
