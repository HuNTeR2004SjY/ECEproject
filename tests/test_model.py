"""
TEST MODEL
==========

This script tests if the trained model is working by giving it a sample input.
Run this after training to verify the model works correctly.

Usage:
    python test_model.py
    python test_model.py --subject "Your subject" --body "Your ticket body"
"""

import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer
from pathlib import Path
import json
import pickle
import argparse


class TriageModel(nn.Module):
    """Multi-task model for ticket triage and tagging."""
    
    def __init__(self, model_name: str, num_types: int, num_queues: int,
                 num_priorities: int, num_unique_tags: int, dropout: float = 0.3):
        super(TriageModel, self).__init__()
        
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        
        self.dropout = nn.Dropout(dropout)
        
        self.type_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, num_types)
        )
        
        self.priority_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, num_priorities)
        )
        
        self.queue_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, num_queues)
        )
        
        self.tag_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, num_unique_tags)
        )
    
    def forward(self, input_ids, attention_mask):
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = outputs.pooler_output
        pooled_output = self.dropout(pooled_output)
        
        type_logits = self.type_classifier(pooled_output)
        priority_logits = self.priority_classifier(pooled_output)
        queue_logits = self.queue_classifier(pooled_output)
        tag_logits = self.tag_classifier(pooled_output)
        
        return type_logits, priority_logits, queue_logits, tag_logits, pooled_output


def load_model(model_dir: str = 'trained_model'):
    """Load the trained model and all required components."""
    model_dir = Path(model_dir)
    
    # Check if model exists
    model_path = model_dir / 'model.pth'
    config_path = model_dir / 'config.json'
    tag_binarizer_path = model_dir / 'tag_binarizer.pkl'
    
    if not model_path.exists():
        print("❌ Model not found!")
        print(f"   Expected model at: {model_path}")
        print("   Please run train_model.py first to train the model.")
        return None, None, None, None
    
    print("✓ Model files found!")
    print(f"  Model path: {model_path}")
    print(f"  Config path: {config_path}")
    
    # Load config
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Load tag binarizer
    with open(tag_binarizer_path, 'rb') as f:
        tag_binarizer = pickle.load(f)
    
    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"  Device: {device}")
    
    # Initialize model
    model = TriageModel(
        model_name=config['model_name'],
        num_types=config['num_types'],
        num_queues=config['num_queues'],
        num_priorities=config['num_priorities'],
        num_unique_tags=config['num_unique_tags']
    )
    
    # Load weights
    checkpoint = torch.load(model_path, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.to(device)
    model.eval()
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(config['model_name'])
    
    print("✓ Model loaded successfully!")
    
    return model, tokenizer, config, tag_binarizer


def predict(model, tokenizer, config, tag_binarizer, subject: str, body: str, device=None):
    """Make predictions for a single ticket."""
    if device is None:
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    # Prepare input
    text = subject + " [SEP] " + body
    
    encoding = tokenizer.encode_plus(
        text,
        add_special_tokens=True,
        max_length=512,
        padding='max_length',
        truncation=True,
        return_attention_mask=True,
        return_tensors='pt'
    )
    
    input_ids = encoding['input_ids'].to(device)
    attention_mask = encoding['attention_mask'].to(device)
    
    # Make prediction
    with torch.no_grad():
        type_logits, priority_logits, queue_logits, tag_logits, _ = model(input_ids, attention_mask)
    
    # Get predictions
    type_pred = type_logits.argmax(1).item()
    priority_pred = priority_logits.argmax(1).item()
    queue_pred = queue_logits.argmax(1).item()
    
    # Get top tags (probability > 0.5)
    tag_probs = torch.sigmoid(tag_logits)
    top_tags_indices = (tag_probs > 0.3).squeeze().nonzero().squeeze()
    
    if top_tags_indices.dim() == 0:
        top_tags_indices = top_tags_indices.unsqueeze(0)
    
    # Convert predictions to labels
    type_label = config['type_classes'][type_pred]
    priority_label = config['priority_classes'][priority_pred]
    queue_label = config['queue_classes'][queue_pred]
    
    # Get tag names
    if len(top_tags_indices) > 0:
        tag_labels = [config['tag_classes'][idx.item()] for idx in top_tags_indices[:5]]
    else:
        # Get top 3 tags by probability
        top_k = torch.topk(tag_probs.squeeze(), 3)
        tag_labels = [config['tag_classes'][idx.item()] for idx in top_k.indices]
    
    # Get confidence scores
    type_conf = torch.softmax(type_logits, dim=1).max().item()
    priority_conf = torch.softmax(priority_logits, dim=1).max().item()
    queue_conf = torch.softmax(queue_logits, dim=1).max().item()
    
    return {
        'type': type_label,
        'type_confidence': type_conf,
        'priority': priority_label,
        'priority_confidence': priority_conf,
        'queue': queue_label,
        'queue_confidence': queue_conf,
        'tags': tag_labels
    }


def main():
    parser = argparse.ArgumentParser(description='Test Trained Triage Model')
    parser.add_argument('--model_dir', type=str, default='trained_model',
                        help='Directory containing the trained model')
    parser.add_argument('--subject', type=str, default=None,
                        help='Ticket subject')
    parser.add_argument('--body', type=str, default=None,
                        help='Ticket body')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("MODEL TEST")
    print("=" * 80)
    
    # Load model
    print("\n1. Loading model...")
    model, tokenizer, config, tag_binarizer = load_model(args.model_dir)
    
    if model is None:
        return
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    # Print model info
    print("\n2. Model Configuration:")
    print(f"   Base model: {config['model_name']}")
    print(f"   Types: {config['type_classes']}")
    print(f"   Priorities: {config['priority_classes']}")
    print(f"   Queues: {len(config['queue_classes'])} queues")
    print(f"   Tags: {config['num_unique_tags']} unique tags")
    
    # Test with sample or user input
    if args.subject and args.body:
        subject = args.subject
        body = args.body
    else:
        # Sample test inputs
        print("\n3. Testing with sample inputs...")
        
        test_cases = [
            {
                "subject": "Cannot login to my account",
                "body": "I've been trying to login to my account for the past hour but keep getting an error message. I've tried resetting my password but it still doesn't work. Please help urgently!"
            },
            {
                "subject": "Billing inquiry about invoice",
                "body": "I received an invoice for $500 but I believe I was charged incorrectly. My subscription is only $25 per month. Can you please review and correct this?"
            },
            {
                "subject": "Feature request for mobile app",
                "body": "It would be great if the mobile app could support dark mode. Many users including myself prefer dark mode especially when using the app at night."
            }
        ]
        
        for i, test in enumerate(test_cases, 1):
            print(f"\n{'=' * 60}")
            print(f"TEST CASE {i}")
            print("=" * 60)
            print(f"Subject: {test['subject']}")
            print(f"Body: {test['body'][:100]}...")
            
            result = predict(model, tokenizer, config, tag_binarizer, 
                           test['subject'], test['body'], device)
            
            print(f"\n📋 PREDICTIONS:")
            print(f"   Type:     {result['type']} (confidence: {result['type_confidence']:.2%})")
            print(f"   Priority: {result['priority']} (confidence: {result['priority_confidence']:.2%})")
            print(f"   Queue:    {result['queue']} (confidence: {result['queue_confidence']:.2%})")
            print(f"   Tags:     {', '.join(result['tags'])}")
        
        print("\n" + "=" * 80)
        print("✅ MODEL TEST COMPLETE - The model is trained and working!")
        print("=" * 80)
        return
    
    # Single prediction with user input
    print("\n3. Making prediction...")
    print(f"   Subject: {subject}")
    print(f"   Body: {body[:100]}...")
    
    result = predict(model, tokenizer, config, tag_binarizer, subject, body, device)
    
    print(f"\n📋 PREDICTIONS:")
    print(f"   Type:     {result['type']} (confidence: {result['type_confidence']:.2%})")
    print(f"   Priority: {result['priority']} (confidence: {result['priority_confidence']:.2%})")
    print(f"   Queue:    {result['queue']} (confidence: {result['queue_confidence']:.2%})")
    print(f"   Tags:     {', '.join(result['tags'])}")
    
    print("\n" + "=" * 80)
    print("✅ MODEL TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
