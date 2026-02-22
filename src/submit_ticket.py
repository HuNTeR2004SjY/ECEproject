"""
SUBMIT A TICKET TO TEST THE MODEL
=================================

Run this script to submit a ticket and see the model's predictions.

Usage:
    python submit_ticket.py
"""

import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer
from pathlib import Path
import json
import pickle


class TriageModel(nn.Module):
    def __init__(self, model_name, num_types, num_queues, num_priorities, num_unique_tags, dropout=0.3):
        super(TriageModel, self).__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.type_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256), nn.ReLU(), nn.Dropout(dropout), nn.Linear(256, num_types)
        )
        self.priority_classifier = nn.Sequential(
            nn.Linear(hidden_size, 256), nn.ReLU(), nn.Dropout(dropout), nn.Linear(256, num_priorities)
        )
        self.queue_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512), nn.ReLU(), nn.Dropout(dropout), nn.Linear(512, num_queues)
        )
        self.tag_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512), nn.ReLU(), nn.Dropout(dropout), nn.Linear(512, num_unique_tags)
        )
    
    def forward(self, input_ids, attention_mask):
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = outputs.pooler_output
        pooled_output = self.dropout(pooled_output)
        return (
            self.type_classifier(pooled_output),
            self.priority_classifier(pooled_output),
            self.queue_classifier(pooled_output),
            self.tag_classifier(pooled_output),
            pooled_output
        )


def load_model():
    """Load the trained model."""
    print("Loading model...")
    
    with open('trained_model/config.json', 'r') as f:
        config = json.load(f)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    model = TriageModel(
        config['model_name'],
        config['num_types'],
        config['num_queues'],
        config['num_priorities'],
        config['num_unique_tags']
    )
    
    checkpoint = torch.load('trained_model/model.pth', map_location=device, weights_only=False)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.to(device)
    model.eval()
    
    tokenizer = AutoTokenizer.from_pretrained(config['model_name'])
    
    print("Model loaded successfully!\n")
    return model, tokenizer, config, device


def predict_ticket(model, tokenizer, config, device, subject, body):
    """Make predictions for a ticket."""
    text = subject + " [SEP] " + body
    
    encoding = tokenizer.encode_plus(
        text,
        add_special_tokens=True,
        max_length=512,
        padding='max_length',
        truncation=True,
        return_tensors='pt'
    )
    
    with torch.no_grad():
        type_logits, priority_logits, queue_logits, tag_logits, _ = model(
            encoding['input_ids'].to(device),
            encoding['attention_mask'].to(device)
        )
    
    # Get predictions
    type_pred = config['type_classes'][type_logits.argmax(1).item()]
    priority_pred = config['priority_classes'][priority_logits.argmax(1).item()]
    queue_pred = config['queue_classes'][queue_logits.argmax(1).item()]
    
    # Get confidence scores
    type_conf = torch.softmax(type_logits, dim=1).max().item()
    priority_conf = torch.softmax(priority_logits, dim=1).max().item()
    queue_conf = torch.softmax(queue_logits, dim=1).max().item()
    
    # Get top tags
    tag_probs = torch.sigmoid(tag_logits)
    top_k = torch.topk(tag_probs.squeeze(), 5)
    tags = [config['tag_classes'][idx.item()] for idx in top_k.indices]
    
    return {
        'type': type_pred,
        'type_confidence': type_conf,
        'priority': priority_pred,
        'priority_confidence': priority_conf,
        'queue': queue_pred,
        'queue_confidence': queue_conf,
        'tags': tags
    }


def main():
    print("=" * 60)
    print("       TICKET TRIAGE MODEL - SUBMIT YOUR TICKET")
    print("=" * 60)
    print()
    
    # Load model
    model, tokenizer, config, device = load_model()
    
    while True:
        print("-" * 60)
        print("Enter your ticket details (or type 'quit' to exit):")
        print("-" * 60)
        
        # Get subject
        subject = input("\nSubject: ").strip()
        if subject.lower() == 'quit':
            print("\nGoodbye!")
            break
        
        # Get body
        print("Body (press Enter twice to submit):")
        body_lines = []
        while True:
            line = input()
            if line == "":
                break
            body_lines.append(line)
        body = " ".join(body_lines)
        
        if not subject or not body:
            print("\nPlease enter both subject and body!")
            continue
        
        # Make prediction
        print("\n" + "=" * 60)
        print("                    PREDICTION RESULTS")
        print("=" * 60)
        
        result = predict_ticket(model, tokenizer, config, device, subject, body)
        
        print(f"\n  Your Ticket:")
        print(f"  Subject: {subject}")
        print(f"  Body: {body[:100]}{'...' if len(body) > 100 else ''}")
        
        print(f"\n  Model Predictions:")
        print(f"  +-----------------+------------------------+------------+")
        print(f"  | Field           | Prediction             | Confidence |")
        print(f"  +-----------------+------------------------+------------+")
        print(f"  | Type            | {result['type']:<22} | {result['type_confidence']:>9.1%} |")
        print(f"  | Priority        | {result['priority']:<22} | {result['priority_confidence']:>9.1%} |")
        print(f"  | Queue           | {result['queue']:<22} | {result['queue_confidence']:>9.1%} |")
        print(f"  +-----------------+------------------------+------------+")
        print(f"\n  Suggested Tags: {', '.join(result['tags'])}")
        print()


if __name__ == "__main__":
    main()
