import sys
import re

try:
    with open('e:/College/main pro/VishnuSide/ECE/static/style.css', 'r', encoding='utf-8') as f:
        content = f.read()

    # Normalizing line endings for reliable replacement
    content = content.replace('\r\n', '\n')
    
    replacements = {
        '--bg-dark: #0f172a;': '--bg-dark: #f8fafc;',
        '--bg-dark: #09090b;': '--bg-dark: #f8fafc;',
        '--bg-card: #1e293b;': '--bg-card: #ffffff;',
        '--bg-card: #18181b;': '--bg-card: #ffffff;',
        '--bg-input: #334155;': '--bg-input: #f1f5f9;',
        '--bg-input: #27272a;': '--bg-input: #f1f5f9;',
        '--text-primary: #f8fafc;': '--text-primary: #0f172a;',
        '--text-primary: #fafafa;': '--text-primary: #0f172a;',
        '--text-secondary: #94a3b8;': '--text-secondary: #475569;',
        '--text-secondary: #a1a1aa;': '--text-secondary: #475569;',
        '--text-muted: #64748b;': '--text-muted: #94a3b8;',
        '--text-muted: #71717a;': '--text-muted: #94a3b8;',
        '--border: #475569;': '--border: #e2e8f0;',
        '--border: #27272a;': '--border: #e2e8f0;',
        
        # Adjusting the background pattern to be more subtle for light theme
        'rgba(99, 102, 241, 0.15)': 'rgba(99, 102, 241, 0.08)',
        'rgba(139, 92, 246, 0.1)': 'rgba(139, 92, 246, 0.05)',
        'rgba(14, 165, 233, 0.05)': 'rgba(14, 165, 233, 0.03)',
        
        # Shadows need adjusting to look right on white
        '--shadow-glow: 0 0 40px rgba(99, 102, 241, 0.3);': '--shadow-glow: 0 0 30px rgba(99, 102, 241, 0.15);',
        
        # Header border
        'border-bottom: 1px solid rgba(255, 255, 255, 0.1);': 'border-bottom: 1px solid var(--border);'
    }

    replaced_count = 0
    for old, new in replacements.items():
        if old in content:
            content = content.replace(old, new)
            print(f'Replaced {old.strip()} successfully.')
            replaced_count += 1
        else:
            print(f'Could not find: {old.strip()}')

    print(f"Total replacements: {replaced_count}")
    
    # Overwrite the file with new content
    with open('e:/College/main pro/VishnuSide/ECE/static/style.css', 'w', encoding='utf-8') as f:
        f.write(content)
        
except Exception as e:
    print(f'Error: {e}')
