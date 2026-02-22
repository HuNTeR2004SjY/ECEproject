print("Starting import...")
try:
    from app import app
    print("Import successful!")
except Exception as e:
    print(f"Import failed: {e}")
