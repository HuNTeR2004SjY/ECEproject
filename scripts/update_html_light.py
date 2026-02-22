import sys
import glob

def update_html_files():
    html_files = [
        'e:/College/main pro/VishnuSide/ECE/templates/index.html',
        'e:/College/main pro/VishnuSide/ECE/templates/admin_dashboard.html'
    ]
    
    replacements = {
        'rgba(255,255,255,0.02)': 'rgba(0,0,0,0.03)',
        'rgba(255, 255, 255, 0.1)': 'rgba(0,0,0,0.05)',
        'rgba(255,255,255,0.1)': 'rgba(0,0,0,0.05)',
        'rgba(255,255,255,0.03)': 'rgba(0,0,0,0.04)',
        'background: rgba(0,0,0,0.6)': 'background: rgba(255,255,255,0.4)',  # Light mode modal backdrop
        'rgba(0, 0, 0, 0.6)': 'rgba(255,255,255,0.4)'
    }

    for file_path in html_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            original = content
            replaced = 0
            for old, new in replacements.items():
                if old in content:
                    content = content.replace(old, new)
                    replaced += 1

            if content != original:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"Updated {file_path} with {replaced} replacements.")
            else:
                print(f"No changes needed in {file_path}.")
                
        except Exception as e:
            print(f"Error processing {file_path}: {e}")

if __name__ == "__main__":
    update_html_files()
