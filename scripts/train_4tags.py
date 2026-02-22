"""
TRAINING SCRIPT - 4 TAGS VERSION
=================================

Key changes from original:
1. Changed from 8 tags to 4 tags
2. Adjusted tag processing in Dataset class
3. Updated tag columns references
"""

# In the TicketDataset class, update to:

class TicketDataset(Dataset):
    """Dataset for ticket training with 4 tags (reduced from 8)."""
    
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
        
        # Multi-label tags - NOW ONLY 4 TAGS
        tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4']  # Reduced from 8
        self.tags = []
        for idx, row in data.iterrows():
            ticket_tags = [row[col] for col in tag_cols if row[col] != '']
            self.tags.append(ticket_tags)
        
        # Transform tags to binary matrix
        self.tag_labels = tag_binarizer.transform(self.tags)


# In the TriageTrainer.load_and_prepare_data method, update to:

def load_and_prepare_data(self, csv_path: str, test_size: float = 0.2):
    """Load processed data and prepare for training."""
    print(f"\nLoading processed data from {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} processed tickets")
    
    # Prepare tag columns - NOW ONLY 4 TAGS
    tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4']  # Reduced from 8
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


# In the _store_training_data method, update to:

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


# In the build_answer_retrieval_index method, update to:

def build_answer_retrieval_index(self, df: pd.DataFrame):
    """Build answer retrieval index using ticket embeddings."""
    print("\n" + "=" * 80)
    print("BUILDING ANSWER RETRIEVAL INDEX")
    print("=" * 80)
    
    self.model.eval()
    
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM answer_embeddings')
    
    batch_size = 32
    tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4']  # Reduced from 8
    
    for i in range(0, len(df), batch_size):
        batch_df = df.iloc[i:i+batch_size]
        
        texts = (batch_df['Subject'].fillna('') + " [SEP] " + 
                batch_df['Body'].fillna('')).astype(str).tolist()
        
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
        
        with torch.no_grad():
            _, _, _, _, embeddings = self.model(input_ids, attention_mask)
        
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
