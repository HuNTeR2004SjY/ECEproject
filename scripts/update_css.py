import sys

try:
    with open('e:/College/main pro/VishnuSide/ECE/static/style.css', 'r', encoding='utf-8') as f:
        content = f.read()

    # Normalizing line endings for reliable replacement
    content = content.replace('\r\n', '\n')
    
    replacements = {
        '.result-card {\n    background: linear-gradient(145deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.02));\n    padding: 25px;\n    border-radius: var(--radius-md);\n    border: 1px solid rgba(255, 255, 255, 0.1);\n    transition: all 0.3s ease;\n}': '.result-card {\n    background: var(--bg-card);\n    padding: 25px;\n    border-radius: var(--radius-md);\n    border: 1px solid var(--border);\n    transition: all 0.3s ease;\n    backdrop-filter: var(--glass-blur);\n    -webkit-backdrop-filter: var(--glass-blur);\n}',
        '.solution-card {\n    background: #ffffff;\n    border-radius: 12px;\n    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);\n    overflow: hidden;\n    border: 1px solid #e5e7eb;\n}': '.solution-card {\n    background: var(--bg-card);\n    border-radius: var(--radius-lg);\n    box-shadow: var(--shadow-xl);\n    overflow: hidden;\n    border: 1px solid var(--border);\n    backdrop-filter: var(--glass-blur);\n    -webkit-backdrop-filter: var(--glass-blur);\n}',
        '.solution-card .card-header {\n    background: #f8fafc;\n    padding: 1rem 1.5rem;\n    border-bottom: 1px solid #e5e7eb;\n    display: flex;\n    align-items: center;\n    gap: 0.75rem;\n}': '.solution-card .card-header {\n    background: rgba(255, 255, 255, 0.03);\n    padding: 1rem 1.5rem;\n    border-bottom: 1px solid var(--border);\n    display: flex;\n    align-items: center;\n    gap: 0.75rem;\n}',
        '.tags-section {\n    background: rgba(255, 255, 255, 0.03);\n    padding: 20px;\n    border-radius: var(--radius-md);\n    border: 1px solid rgba(255, 255, 255, 0.05);\n}': '.tags-section {\n    background: rgba(255, 255, 255, 0.02);\n    padding: 20px;\n    border-radius: var(--radius-md);\n    border: 1px solid var(--border);\n    backdrop-filter: var(--glass-blur);\n    -webkit-backdrop-filter: var(--glass-blur);\n}',
        '.confidence-bar {\n    flex: 1;\n    height: 8px;\n    background: rgba(255, 255, 255, 0.1);\n    border-radius: 10px;\n    overflow: hidden;\n}': '.confidence-bar {\n    flex: 1;\n    height: 8px;\n    background: rgba(255, 255, 255, 0.05);\n    border-radius: 10px;\n    overflow: hidden;\n    box-shadow: inset 0 1px 3px rgba(0,0,0,0.2);\n}',
        '.tag {\n    display: inline-flex;\n    align-items: center;\n    gap: 6px;\n    background: rgba(99, 102, 241, 0.2);\n    color: var(--primary-light);\n    padding: 8px 16px;\n    border-radius: 50px;\n    font-size: 0.85rem;\n    font-weight: 500;\n    border: 1px solid rgba(99, 102, 241, 0.3);\n    transition: all 0.3s ease;\n}': '.tag {\n    display: inline-flex;\n    align-items: center;\n    gap: 6px;\n    background: rgba(129, 140, 248, 0.1);\n    color: var(--primary-light);\n    padding: 8px 16px;\n    border-radius: 50px;\n    font-size: 0.85rem;\n    font-weight: 500;\n    border: 1px solid rgba(129, 140, 248, 0.2);\n    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);\n}'
    }

    replaced_count = 0
    for old, new in replacements.items():
        if old in content:
            content = content.replace(old, new)
            print(f'Replaced a block successfully: {old[:20]}...')
            replaced_count += 1
        else:
            print(f'Could not find block starting with: {old[:30].replace(chr(10), "")}...')

    print(f"Total replacements: {replaced_count}")
    with open('e:/College/main pro/VishnuSide/ECE/static/style.css', 'w', encoding='utf-8') as f:
        f.write(content)
        
except Exception as e:
    print(f'Error: {e}')
