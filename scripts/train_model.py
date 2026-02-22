"""
STEP 2: MODEL TRAINING
======================

This script trains a multi-task model that:
1. Classifies tickets into type, queue, and priority
2. Predicts multiple tags (multi-label classification)
3. Builds a retrieval system for answer generation

The model uses:
- BERT encoder for understanding ticket text
- Multiple classification heads for different tasks
- Vector database for answer retrieval

Run this AFTER preprocessing your data.
"""

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup
from torch.optim import AdamW
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, MultiLabelBinarizer
from sklearn.utils.class_weight import compute_class_weight
from pathlib import Path
import json
import pickle
import argparse
from collections import Counter
import sqlite3


# ============================================================================
# MODEL ARCHITECTURE
# ============================================================================

class TriageModel(nn.Module):
    """
    Multi-task model for ticket triage and tagging.
    
    Architecture:
    - Shared BERT encoder
    - Separate heads for: type, queue, priority
    - Multi-label head for tags (8 tag slots)
    """
    
    def __init__(self, model_name: str, num_types: int, num_queues: int,
                 num_priorities: int, num_unique_tags: int, dropout: float = 0.3):
        super(TriageModel, self).__init__()
        
        # Shared encoder - learns ticket representations
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        
        self.dropout = nn.Dropout(dropout)
        
        # Classification heads for single-label tasks
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
            nn.Linear(hidden_size, 512),  # More capacity for many queues
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, num_queues)
        )
        
        # Multi-label tag classifier
        # Predicts probability for each possible tag
        self.tag_classifier = nn.Sequential(
            nn.Linear(hidden_size, 512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, num_unique_tags)
        )
    
    def forward(self, input_ids, attention_mask):
        """
        Forward pass returning predictions for all tasks.
        """
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = outputs.pooler_output
        pooled_output = self.dropout(pooled_output)
        
        # Get predictions from each head
        type_logits = self.type_classifier(pooled_output)
        priority_logits = self.priority_classifier(pooled_output)
        queue_logits = self.queue_classifier(pooled_output)
        tag_logits = self.tag_classifier(pooled_output)
        
        return type_logits, priority_logits, queue_logits, tag_logits, pooled_output


# ============================================================================
# DATASET CLASS
# ============================================================================

class TicketDataset(Dataset):
    """Dataset for ticket training with multi-task outputs."""
    
    def __init__(self, data: pd.DataFrame, tokenizer, tag_binarizer,
                 max_length: int = 512):
        self.tokenizer = tokenizer
        self.max_length = max_length
        
        # Combine subject and body
        self.texts = (data['Subject'] + " [SEP] " + data['Body']).values
        
        # Single-label classifications
        self.types = data['type_encoded'].values
        self.priorities = data['priority_encoded'].values
        self.queues = data['queue_encoded'].values
        
        # Multi-label tags
        # Extract all tags for each ticket into a list
        tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8']
        self.tags = []
        for idx, row in data.iterrows():
            ticket_tags = [row[col] for col in tag_cols if row[col] != '']
            self.tags.append(ticket_tags)
        
        # Transform tags to binary matrix
        self.tag_labels = tag_binarizer.transform(self.tags)
    
    def __len__(self):
        return len(self.texts)
    
    def __getitem__(self, idx):
        text = str(self.texts[idx])
        
        encoding = self.tokenizer.encode_plus(
            text,
            add_special_tokens=True,
            max_length=self.max_length,
            padding='max_length',
            truncation=True,
            return_attention_mask=True,
            return_tensors='pt'
        )
        
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'type': torch.tensor(self.types[idx], dtype=torch.long),
            'priority': torch.tensor(self.priorities[idx], dtype=torch.long),
            'queue': torch.tensor(self.queues[idx], dtype=torch.long),
            'tags': torch.tensor(self.tag_labels[idx], dtype=torch.float)
        }


# ============================================================================
# TRAINING MANAGER
# ============================================================================

class TriageTrainer:
    """Manages the complete training pipeline."""
    
    def __init__(self, model_name: str = 'bert-base-uncased',
                 db_path: str = 'tickets.db',
                 output_dir: str = 'trained_model'):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        print(f"Using device: {self.device}")
        
        self.model_name = model_name
        self.db_path = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        
        # Label encoders for single-label tasks
        self.label_encoders = {
            'type': LabelEncoder(),
            'priority': LabelEncoder(),
            'queue': LabelEncoder()
        }
        
        # Multi-label binarizer for tags
        self.tag_binarizer = MultiLabelBinarizer()
        
        self.class_weights = {
            'type': None,
            'priority': None,
            'queue': None
        }
        
        self.model = None
        self._init_database()
    
    def _init_database(self):
        """Initialize database for storing training data and answers."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Training memory for continual learning
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS training_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                answer TEXT,
                type TEXT,
                priority TEXT,
                queue TEXT,
                tags TEXT,
                added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Answer embeddings for retrieval
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS answer_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                answer TEXT,
                embedding BLOB,
                type TEXT,
                queue TEXT,
                tags TEXT
            )
        ''')
        
        # Learning buffer for continual learning
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                answer TEXT,
                type TEXT,
                priority TEXT,
                queue TEXT,
                tags TEXT,
                added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                used_for_training BOOLEAN DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def load_and_prepare_data(self, csv_path: str, test_size: float = 0.2):
        """Load processed data and prepare for training."""
        print(f"\nLoading processed data from {csv_path}...")
        df = pd.read_csv(csv_path)
        print(f"Loaded {len(df)} processed tickets")
        
        # Prepare tag columns
        tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8']
        for col in tag_cols:
            df[col] = df[col].fillna('')
        
        # Rename columns to match expected format
        df = df.rename(columns={
            'subject': 'Subject',
            'body': 'Body',
            'answer': 'Answer',
            'type': 'Type',
            'priority': 'Priority',
            'queue': 'Queue'
        })
        
        print("\nEncoding labels...")
        
        # Encode single-label classifications
        df['type_encoded'] = self.label_encoders['type'].fit_transform(df['Type'])
        df['priority_encoded'] = self.label_encoders['priority'].fit_transform(df['Priority'])
        df['queue_encoded'] = self.label_encoders['queue'].fit_transform(df['Queue'])
        
        # Prepare tags for multi-label encoding
        # Collect all tags from all tickets
        all_tags = []
        for _, row in df.iterrows():
            ticket_tags = [row[col] for col in tag_cols if row[col] != '']
            all_tags.append(ticket_tags)
        
        # Fit the multi-label binarizer
        self.tag_binarizer.fit(all_tags)
        
        print(f"\nDataset statistics:")
        print(f"  Types: {len(self.label_encoders['type'].classes_)}")
        print(f"  Priorities: {len(self.label_encoders['priority'].classes_)}")
        print(f"  Queues: {len(self.label_encoders['queue'].classes_)}")
        print(f"  Unique tags: {len(self.tag_binarizer.classes_)}")
        print(f"  Top tags: {list(self.tag_binarizer.classes_[:10])}")
        
        # Split data
        train_df, val_df = train_test_split(
            df,
            test_size=test_size,
            random_state=42,
            stratify=df['queue_encoded']
        )
        
        print(f"\nSplit: {len(train_df)} train, {len(val_df)} validation")
        
        # Store training data in database
        self._store_training_data(train_df, tag_cols)
        
        return train_df, val_df
    
    def _store_training_data(self, df: pd.DataFrame, tag_cols: list):
        """Store training data for continual learning."""
        print("\nStoring training data in database...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for _, row in df.iterrows():
            tags = [row[col] for col in tag_cols if row[col] != '']
            tags_str = json.dumps(tags)
            
            cursor.execute('''
                INSERT INTO training_memory 
                (subject, body, answer, type, priority, queue, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['Subject'], row['Body'], row['Answer'],
                row['Type'], row['Priority'], row['Queue'], tags_str
            ))
        
        conn.commit()
        conn.close()
        print(f"✓ Stored {len(df)} training examples")
    
    def _compute_class_weights(self, labels: np.ndarray) -> torch.Tensor:
        """Compute class weights for imbalanced data."""
        unique_classes = np.unique(labels)
        weights = compute_class_weight(
            class_weight='balanced',
            classes=unique_classes,
            y=labels
        )
        return torch.tensor(weights, dtype=torch.float32)
    
    def train(self, train_df: pd.DataFrame, val_df: pd.DataFrame,
              epochs: int = 3, batch_size: int = 16, learning_rate: float = 2e-5):
        """Train the multi-task model."""
        
        print("\n" + "=" * 80)
        print("STARTING TRAINING")
        print("=" * 80)
        
        # Create datasets
        train_dataset = TicketDataset(train_df, self.tokenizer, self.tag_binarizer)
        val_dataset = TicketDataset(val_df, self.tokenizer, self.tag_binarizer)
        
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size)
        
        # Compute class weights
        print("\nComputing class weights...")
        self.class_weights['type'] = self._compute_class_weights(
            train_df['type_encoded'].values
        ).to(self.device)
        self.class_weights['priority'] = self._compute_class_weights(
            train_df['priority_encoded'].values
        ).to(self.device)
        self.class_weights['queue'] = self._compute_class_weights(
            train_df['queue_encoded'].values
        ).to(self.device)
        
        # Initialize model
        print("\nInitializing model...")
        self.model = TriageModel(
            model_name=self.model_name,
            num_types=len(self.label_encoders['type'].classes_),
            num_queues=len(self.label_encoders['queue'].classes_),
            num_priorities=len(self.label_encoders['priority'].classes_),
            num_unique_tags=len(self.tag_binarizer.classes_)
        ).to(self.device)
        
        # Optimizer and scheduler
        optimizer = AdamW(self.model.parameters(), lr=learning_rate)
        total_steps = len(train_loader) * epochs
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=int(0.1 * total_steps),
            num_training_steps=total_steps
        )
        
        # Loss functions
        criterion_type = nn.CrossEntropyLoss(weight=self.class_weights['type'])
        criterion_priority = nn.CrossEntropyLoss(weight=self.class_weights['priority'])
        criterion_queue = nn.CrossEntropyLoss(weight=self.class_weights['queue'])
        criterion_tags = nn.BCEWithLogitsLoss()  # For multi-label
        
        # Training loop
        best_val_loss = float('inf')
        
        for epoch in range(epochs):
            print(f"\n{'=' * 80}")
            print(f"EPOCH {epoch + 1}/{epochs}")
            print("=" * 80)
            
            self.model.train()
            total_loss = 0
            batch_count = 0
            
            for batch in train_loader:
                input_ids = batch['input_ids'].to(self.device)
                attention_mask = batch['attention_mask'].to(self.device)
                type_label = batch['type'].to(self.device)
                priority = batch['priority'].to(self.device)
                queue = batch['queue'].to(self.device)
                tags = batch['tags'].to(self.device)
                
                # Forward pass
                type_logits, priority_logits, queue_logits, tag_logits, _ = \
                    self.model(input_ids, attention_mask)
                
                # Compute losses
                loss_type = criterion_type(type_logits, type_label)
                loss_priority = criterion_priority(priority_logits, priority)
                loss_queue = criterion_queue(queue_logits, queue)
                loss_tags = criterion_tags(tag_logits, tags)
                
                # Combined loss (you can adjust weights)
                loss = loss_type + loss_priority + loss_queue + 0.5 * loss_tags
                
                # Backward pass
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                
                total_loss += loss.item()
                batch_count += 1
                
                if batch_count % 50 == 0:
                    print(f"  Batch {batch_count}/{len(train_loader)} | Loss: {total_loss/batch_count:.4f}")
            
            avg_loss = total_loss / len(train_loader)
            print(f"\nTraining Loss: {avg_loss:.4f}")
            
            # Validation
            val_loss = self._validate(val_loader, criterion_type, criterion_priority,
                                     criterion_queue, criterion_tags)
            
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                print(f"✓ New best model! Validation loss: {val_loss:.4f}")
        
        print("\n" + "=" * 80)
        print(f"TRAINING COMPLETE - Best validation loss: {best_val_loss:.4f}")
        print("=" * 80)
    
    def _validate(self, val_loader, criterion_type, criterion_priority,
                  criterion_queue, criterion_tags):
        """Validate the model."""
        self.model.eval()
        total_loss = 0
        correct = {'type': 0, 'priority': 0, 'queue': 0}
        total = 0
        
        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch['input_ids'].to(self.device)
                attention_mask = batch['attention_mask'].to(self.device)
                type_label = batch['type'].to(self.device)
                priority = batch['priority'].to(self.device)
                queue = batch['queue'].to(self.device)
                tags = batch['tags'].to(self.device)
                
                type_logits, priority_logits, queue_logits, tag_logits, _ = \
                    self.model(input_ids, attention_mask)
                
                # Calculate losses
                loss = (criterion_type(type_logits, type_label) +
                       criterion_priority(priority_logits, priority) +
                       criterion_queue(queue_logits, queue) +
                       0.5 * criterion_tags(tag_logits, tags))
                
                total_loss += loss.item()
                
                # Calculate accuracies
                correct['type'] += (type_logits.argmax(1) == type_label).sum().item()
                correct['priority'] += (priority_logits.argmax(1) == priority).sum().item()
                correct['queue'] += (queue_logits.argmax(1) == queue).sum().item()
                
                total += len(type_label)
        
        avg_loss = total_loss / len(val_loader)
        
        print(f"\nValidation Results:")
        print(f"  Loss: {avg_loss:.4f}")
        print(f"  Type accuracy: {correct['type']/total:.4f}")
        print(f"  Priority accuracy: {correct['priority']/total:.4f}")
        print(f"  Queue accuracy: {correct['queue']/total:.4f}")
        
        return avg_loss
    
    def build_answer_retrieval_index(self, df: pd.DataFrame):
        """
        Build answer retrieval index using ticket embeddings.
        This allows us to find similar tickets and retrieve their answers.
        """
        print("\n" + "=" * 80)
        print("BUILDING ANSWER RETRIEVAL INDEX")
        print("=" * 80)
        
        self.model.eval()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing embeddings
        cursor.execute('DELETE FROM answer_embeddings')
        
        batch_size = 32
        tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8']
        
        for i in range(0, len(df), batch_size):
            batch_df = df.iloc[i:i+batch_size]
            
            # Prepare texts - handle NaN values
            texts = (batch_df['Subject'].fillna('') + " [SEP] " + batch_df['Body'].fillna('')).astype(str).tolist()
            
            # Tokenize
            encodings = self.tokenizer.batch_encode_plus(
                texts,
                add_special_tokens=True,
                max_length=512,
                padding='max_length',
                truncation=True,
                return_attention_mask=True,
                return_tensors='pt'
            )
            
            input_ids = encodings['input_ids'].to(self.device)
            attention_mask = encodings['attention_mask'].to(self.device)
            
            # Get embeddings
            with torch.no_grad():
                _, _, _, _, embeddings = self.model(input_ids, attention_mask)
            
            # Store embeddings
            embeddings = embeddings.cpu().numpy()
            
            for j, (idx, row) in enumerate(batch_df.iterrows()):
                tags = [row[col] for col in tag_cols if row[col] != '']
                tags_str = json.dumps(tags)
                
                embedding_bytes = pickle.dumps(embeddings[j])
                
                cursor.execute('''
                    INSERT INTO answer_embeddings
                    (ticket_id, answer, embedding, type, queue, tags)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    int(idx), row['Answer'], embedding_bytes,
                    row['Type'], row['Queue'], tags_str
                ))
            
            if (i // batch_size + 1) % 10 == 0:
                print(f"  Processed {i + len(batch_df)}/{len(df)} tickets")
        
        conn.commit()
        conn.close()
        
        print(f"✓ Built retrieval index with {len(df)} answers")
    
    def save_model(self):
        """Save trained model and all metadata."""
        print(f"\nSaving model to {self.output_dir}...")
        
        # Save model weights
        model_path = self.output_dir / 'model.pth'
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'label_encoders': self.label_encoders,
            'class_weights': self.class_weights,
            'model_name': self.model_name
        }, model_path)
        
        # Save tag binarizer separately
        tag_binarizer_path = self.output_dir / 'tag_binarizer.pkl'
        with open(tag_binarizer_path, 'wb') as f:
            pickle.dump(self.tag_binarizer, f)
        
        # Save configuration
        config = {
            'model_name': self.model_name,
            'num_types': len(self.label_encoders['type'].classes_),
            'num_priorities': len(self.label_encoders['priority'].classes_),
            'num_queues': len(self.label_encoders['queue'].classes_),
            'num_unique_tags': len(self.tag_binarizer.classes_),
            'type_classes': self.label_encoders['type'].classes_.tolist(),
            'priority_classes': self.label_encoders['priority'].classes_.tolist(),
            'queue_classes': self.label_encoders['queue'].classes_.tolist(),
            'tag_classes': self.tag_binarizer.classes_.tolist()
        }
        
        config_path = self.output_dir / 'config.json'
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"✓ Model saved successfully")
        print(f"  Model weights: {model_path}")
        print(f"  Tag binarizer: {tag_binarizer_path}")
        print(f"  Configuration: {config_path}")


def main():
    parser = argparse.ArgumentParser(description='Train Triage Model')
    
    parser.add_argument('--data', type=str, required=False,
                       help='Path to processed CSV file')
    parser.add_argument('--model_name', type=str, default='bert-base-uncased',
                       help='Pretrained model to use')
    parser.add_argument('--epochs', type=int, default=3,
                       help='Number of training epochs')
    parser.add_argument('--batch_size', type=int, default=16,
                       help='Training batch size')
    parser.add_argument('--learning_rate', type=float, default=2e-5,
                       help='Learning rate')
    parser.add_argument('--output_dir', type=str, default='trained_model',
                       help='Output directory for model')
    parser.add_argument('--db_path', type=str, default='tickets.db',
                       help='Database path')
    parser.add_argument('--skip-training', action='store_true',
                        help='Skip training and only rebuild answer retrieval index')
    
    args = parser.parse_args()
    
    # Prompt for data path if not provided
    if args.data is None:
        print("Available CSV files in current directory:")
        import os
        csv_files = [f for f in os.listdir('.') if f.endswith('.csv')]
        for i, f in enumerate(csv_files, 1):
            print(f"  {i}. {f}")
        print()
        args.data = input("📝 Enter path to training data CSV file: ").strip()
        if not args.data:
            print("❌ No data file provided. Exiting.")
            return
    
    print("=" * 80)
    print("TRIAGE MODEL TRAINING")
    print("=" * 80)
    
    trainer = TriageTrainer(
        model_name=args.model_name,
        db_path=args.db_path,
        output_dir=args.output_dir
    )
    
    # Load and prepare data
    train_df, val_df = trainer.load_and_prepare_data(args.data)
    
    if args.skip_training:
        # Load existing model instead of training
        print("\nSkipping training, loading existing model...")
        model_path = Path(args.output_dir) / 'model.pth'
        tag_binarizer_path = Path(args.output_dir) / 'tag_binarizer.pkl'
        
        if not model_path.exists():
            raise FileNotFoundError(f"No saved model found at {model_path}. Run training first.")
        
        checkpoint = torch.load(model_path, map_location=trainer.device)
        
        # Load tag binarizer
        with open(tag_binarizer_path, 'rb') as f:
            trainer.tag_binarizer = pickle.load(f)
        
        # Initialize and load model
        trainer.model = TriageModel(
            model_name=checkpoint['model_name'],
            num_types=len(trainer.label_encoders['type'].classes_),
            num_queues=len(trainer.label_encoders['queue'].classes_),
            num_priorities=len(trainer.label_encoders['priority'].classes_),
            num_unique_tags=len(trainer.tag_binarizer.classes_)
        ).to(trainer.device)
        trainer.model.load_state_dict(checkpoint['model_state_dict'])
        print("✓ Model loaded successfully")
    else:
        # Train model
        trainer.train(train_df, val_df, args.epochs, args.batch_size, args.learning_rate)
        # Save model
        trainer.save_model()
    
    # Build answer retrieval index
    trainer.build_answer_retrieval_index(train_df)
    
    print("\n" + "=" * 80)
    print("TRAINING COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()