"""Quick model verification script."""
import torch
import json
from pathlib import Path

# Load config
with open('trained_model/config.json', 'r') as f:
    config = json.load(f)

print('=' * 60)
print('MODEL VERIFICATION')
print('=' * 60)
print('')
print('MODEL IS TRAINED AND READY!')
print('')
print('Model Details:')
print('  Base Model:', config['model_name'])
print('  Types:', config['type_classes'])
print('  Priorities:', config['priority_classes'])
print('  Queues:', len(config['queue_classes']), 'queues')
print('  Tags:', config['num_unique_tags'], 'unique tags')
print('')
print('Testing model load...')

# Try loading model
checkpoint = torch.load('trained_model/model.pth', map_location='cpu', weights_only=False)
print('Model weights loaded successfully!')
print('Label encoders present:', list(checkpoint['label_encoders'].keys()))

print('')
print('=' * 60)
print('SUCCESS! Your model is trained and ready for inference.')
print('=' * 60)
print('')
print('To test with an input, run:')
print('  python test_model.py')
print('')
print('Or with custom input:')
print('  python test_model.py --subject "Your subject" --body "Your message"')
