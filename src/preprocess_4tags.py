"""
MODIFIED PREPROCESSING - 4 TAGS ONLY
=====================================

Changes:
1. Reduced from 8 tags to 4 tags
2. Added tag relevance scoring
3. Selects most relevant tags based on frequency and co-occurrence
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from collections import Counter
import argparse


class TicketDataPreprocessor:
    """
    Enhanced preprocessor that selects top 4 most relevant tags per ticket.
    """
    
    def __init__(self):
        self.stats = {
            'original_count': 0,
            'final_count': 0,
            'removed_duplicates': 0,
            'removed_invalid': 0,
            'tag_distribution': {},
            'queue_distribution': {},
            'type_distribution': {},
            'priority_distribution': {}
        }
        self.tag_frequencies = {}  # Global tag frequency
        self.tag_cooccurrence = {}  # Tag co-occurrence patterns
    
    def load_data(self, csv_path: str) -> pd.DataFrame:
        """Load the raw CSV file."""
        print(f"Loading data from {csv_path}...")
        df = pd.read_csv(csv_path)
        self.stats['original_count'] = len(df)
        print(f"✓ Loaded {len(df)} tickets")
        print(f"  Columns: {list(df.columns)}")
        return df
    
    def select_required_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Select required columns (now with 8 tag columns initially)."""
        # We'll still load all 8 tags initially to calculate relevance
        required_cols = [
            'subject', 'body', 'answer', 'type', 'queue', 'priority',
            'tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8'
        ]
        
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        
        print("\nSelecting required columns...")
        df = df[required_cols].copy()
        print(f"✓ Selected {len(required_cols)} columns")
        
        return df
    
    def clean_text_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean subject, body, and answer text fields."""
        print("\nCleaning text fields...")
        
        initial_count = len(df)
        
        # Fill NaN with empty strings
        df['subject'] = df['subject'].fillna('')
        df['body'] = df['body'].fillna('')
        df['answer'] = df['answer'].fillna('')
        
        # Replace "nan" string with empty string
        df['subject'] = df['subject'].astype(str).replace('nan', '').replace('NaN', '')
        df['body'] = df['body'].astype(str).replace('nan', '').replace('NaN', '')
        df['answer'] = df['answer'].astype(str).replace('nan', '').replace('NaN', '')
        
        # Strip whitespace
        df['subject'] = df['subject'].str.strip()
        df['body'] = df['body'].str.strip()
        df['answer'] = df['answer'].str.strip()
        
        # Remove tickets with empty subject AND body
        df = df[(df['subject'] != '') | (df['body'] != '')]
        
        # Remove tickets with no answer
        df = df[df['answer'] != '']
        
        removed = initial_count - len(df)
        if removed > 0:
            print(f"  ⚠️  Removed {removed} tickets with missing essential text")
        
        # Remove extremely short/long tickets
        df = df[df['body'].str.len() >= 10]
        df = df[df['body'].str.len() <= 5000]
        df = df[df['answer'].str.len() <= 2000]
        
        total_removed = initial_count - len(df)
        self.stats['removed_invalid'] = total_removed
        
        print(f"✓ Cleaned text fields")
        if total_removed > 0:
            print(f"  Total removed: {total_removed} tickets")
        
        return df
    
    def remove_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove duplicate tickets."""
        print("\nChecking for duplicates...")
        
        initial_count = len(df)
        df = df.drop_duplicates(subset=['subject', 'body'], keep='first')
        
        removed = initial_count - len(df)
        self.stats['removed_duplicates'] = removed
        
        if removed > 0:
            print(f"  ⚠️  Removed {removed} duplicate tickets")
        else:
            print(f"✓ No duplicates found")
        
        return df
    
    def normalize_priority(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize priority values."""
        print("\nNormalizing priority values...")
        
        priority_map = {
            'Low': 'Low', 'Medium': 'Medium', 'High': 'High', 'Critical': 'High',
            '1': 'Low', '2': 'Medium', '3': 'High',
            'low': 'Low', 'medium': 'Medium', 'high': 'High', 'critical': 'High',
            'urgent': 'High', 'normal': 'Medium', 'minor': 'Low'
        }
        
        df['priority'] = df['priority'].astype(str).str.strip()
        df['priority'] = df['priority'].map(lambda x: priority_map.get(x, priority_map.get(x.lower(), x)))
        
        valid_priorities = ['Low', 'Medium', 'High']
        invalid_mask = ~df['priority'].isin(valid_priorities)
        invalid_count = invalid_mask.sum()
        
        if invalid_count > 0:
            print(f"  ⚠️  Removing {invalid_count} tickets with invalid priority")
            df = df[~invalid_mask]
        
        priority_dist = df['priority'].value_counts().to_dict()
        self.stats['priority_distribution'] = priority_dist
        print(f"  Priority distribution: {priority_dist}")
        
        return df
    
    def calculate_tag_frequencies(self, df: pd.DataFrame):
        """Calculate global tag frequencies for relevance scoring."""
        print("\nCalculating tag frequencies...")
        
        tag_columns = ['tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8']
        
        all_tags = []
        for col in tag_columns:
            tags = df[col].fillna('').astype(str).str.strip()
            tags = tags[tags != ''].tolist()
            all_tags.extend(tags)
        
        self.tag_frequencies = Counter(all_tags)
        print(f"  Found {len(self.tag_frequencies)} unique tags")
        print(f"  Top 10 tags: {dict(list(self.tag_frequencies.most_common(10)))}")
    
    def select_top_4_relevant_tags(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Select top 4 most relevant tags per ticket based on:
        1. Tag frequency (more common = more relevant)
        2. Tag position (earlier tags might be more important)
        3. Remove empty tags
        """
        print("\n🎯 Selecting top 4 most relevant tags per ticket...")
        
        tag_columns = ['tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7', 'tag_8']
        
        def score_and_select_tags(row):
            """Score tags and select top 4."""
            tags_with_scores = []
            
            for idx, col in enumerate(tag_columns):
                tag = str(row[col]).strip()
                if tag and tag != '' and tag != 'nan':
                    # Score based on frequency (log scale to reduce dominance)
                    frequency_score = np.log1p(self.tag_frequencies.get(tag, 1))
                    
                    # Position score (earlier tags get slight boost)
                    position_score = (8 - idx) * 0.1
                    
                    # Combined score
                    total_score = frequency_score + position_score
                    
                    tags_with_scores.append((tag, total_score))
            
            # Sort by score (descending) and take top 4
            tags_with_scores.sort(key=lambda x: x[1], reverse=True)
            top_tags = [tag for tag, score in tags_with_scores[:4]]
            
            # Pad with empty strings if less than 4
            while len(top_tags) < 4:
                top_tags.append('')
            
            return pd.Series(top_tags, index=['tag_1', 'tag_2', 'tag_3', 'tag_4'])
        
        # Apply tag selection
        print("  Processing tags...")
        selected_tags = df.apply(score_and_select_tags, axis=1)
        
        # Replace old tag columns with new ones
        df = df.drop(columns=tag_columns)
        df = pd.concat([df, selected_tags], axis=1)
        
        # Remove old tag columns that are no longer needed
        # Keep only tag_1 through tag_4
        
        print("✓ Selected top 4 most relevant tags per ticket")
        
        # Statistics
        tags_per_ticket = df[['tag_1', 'tag_2', 'tag_3', 'tag_4']].apply(
            lambda row: (row != '').sum(), axis=1
        )
        avg_tags = tags_per_ticket.mean()
        print(f"  Average tags per ticket: {avg_tags:.2f}")
        print(f"  Tickets with 4 tags: {(tags_per_ticket == 4).sum()}")
        print(f"  Tickets with 3 tags: {(tags_per_ticket == 3).sum()}")
        print(f"  Tickets with 2 tags: {(tags_per_ticket == 2).sum()}")
        print(f"  Tickets with 1 tag: {(tags_per_ticket == 1).sum()}")
        
        return df
    
    def validate_required_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure all required fields are valid."""
        print("\nValidating required fields...")
        
        initial_count = len(df)
        
        df = df[df['type'].notna() & (df['type'].astype(str).str.strip() != '')]
        df = df[df['queue'].notna() & (df['queue'].astype(str).str.strip() != '')]
        
        removed = initial_count - len(df)
        if removed > 0:
            print(f"  ⚠️  Removed {removed} tickets with missing type or queue")
        else:
            print(f"✓ All tickets have valid type and queue")
        
        return df
    
    def generate_statistics(self, df: pd.DataFrame):
        """Generate and display dataset statistics."""
        print("\n" + "=" * 80)
        print("DATASET STATISTICS")
        print("=" * 80)
        
        self.stats['final_count'] = len(df)
        
        # Type distribution
        type_dist = df['type'].value_counts().to_dict()
        self.stats['type_distribution'] = type_dist
        print("\nType Distribution:")
        for type_val, count in sorted(type_dist.items(), key=lambda x: x[1], reverse=True):
            print(f"  {type_val}: {count} ({count/len(df)*100:.1f}%)")
        
        # Queue distribution
        queue_dist = df['queue'].value_counts().to_dict()
        self.stats['queue_distribution'] = dict(list(queue_dist.items())[:10])
        print("\nQueue Distribution (Top 10):")
        for queue, count in list(queue_dist.items())[:10]:
            print(f"  {queue}: {count} ({count/len(df)*100:.1f}%)")
        
        # Priority distribution
        print("\nPriority Distribution:")
        for priority, count in sorted(self.stats['priority_distribution'].items(), 
                                     key=lambda x: ['High', 'Medium', 'Low'].index(x[0]) if x[0] in ['High', 'Medium', 'Low'] else 999):
            print(f"  {priority}: {count} ({count/len(df)*100:.1f}%)")
        
        # Tag statistics
        print("\nTag Statistics (Top 4 per ticket):")
        tag_cols = ['tag_1', 'tag_2', 'tag_3', 'tag_4']
        all_tags = []
        for col in tag_cols:
            tags = df[col][df[col] != ''].tolist()
            all_tags.extend(tags)
        
        tag_counts = Counter(all_tags)
        self.stats['tag_distribution'] = dict(tag_counts.most_common(20))
        print(f"  Total unique tags: {len(tag_counts)}")
        print(f"  Most common tags:")
        for tag, count in tag_counts.most_common(10):
            print(f"    {tag}: {count}")
    
    def save_processed_data(self, df: pd.DataFrame, output_path: str):
        """Save the cleaned and processed dataset."""
        print(f"\nSaving processed data to {output_path}...")
        df.to_csv(output_path, index=False)
        print(f"✓ Saved {len(df)} processed tickets")
        
        # Save statistics
        stats_path = output_path.replace('.csv', '_stats.json')
        with open(stats_path, 'w') as f:
            json.dump(self.stats, f, indent=2)
        print(f"✓ Saved statistics to {stats_path}")
    
    def process(self, input_csv: str, output_csv: str):
        """Complete preprocessing pipeline."""
        print("=" * 80)
        print("TICKET DATA PREPROCESSING - 4 TAGS VERSION")
        print("=" * 80)
        
        # Load data
        df = self.load_data(input_csv)
        
        # Select required columns
        df = self.select_required_columns(df)
        
        # Clean text fields
        df = self.clean_text_fields(df)
        
        # Remove duplicates
        df = self.remove_duplicates(df)
        
        # Normalize priority
        df = self.normalize_priority(df)
        
        # Calculate tag frequencies (needed for relevance scoring)
        self.calculate_tag_frequencies(df)
        
        # Select top 4 most relevant tags
        df = self.select_top_4_relevant_tags(df)
        
        # Validate required fields
        df = self.validate_required_fields(df)
        
        # Generate statistics
        self.generate_statistics(df)
        
        # Save processed data
        self.save_processed_data(df, output_csv)
        
        print("\n" + "=" * 80)
        print("PREPROCESSING COMPLETE")
        print("=" * 80)
        print(f"\nSummary:")
        print(f"  Original tickets: {self.stats['original_count']}")
        print(f"  Duplicates removed: {self.stats['removed_duplicates']}")
        print(f"  Invalid tickets removed: {self.stats['removed_invalid']}")
        print(f"  Final dataset: {self.stats['final_count']} tickets")
        print(f"  Reduction: {(1 - self.stats['final_count']/self.stats['original_count'])*100:.1f}%")
        print(f"\n✓ Processed data saved to: {output_csv}")
        print("✓ Now using 4 tags per ticket (most relevant)")
        print("Ready for training!")


def main():
    parser = argparse.ArgumentParser(
        description='Preprocess customer support tickets - 4 tags version'
    )
    
    parser.add_argument('--input', type=str, required=True,
                       help='Path to raw CSV file')
    parser.add_argument('--output', type=str, default='processed_tickets_4tags.csv',
                       help='Path for processed output CSV (default: processed_tickets_4tags.csv)')
    
    args = parser.parse_args()
    
    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' not found!")
        return
    
    # Run preprocessing
    preprocessor = TicketDataPreprocessor()
    preprocessor.process(args.input, args.output)


if __name__ == "__main__":
    main()
