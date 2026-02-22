
import sys
import os

# Add project root to path
sys.path.append('d:/Project/ECE')

print("Testing app integration...")

try:
    from app import app, init_solver, solver
    print("✅ Successfully imported app")
    
    # Mock request context or just test function if possible
    # But predict() needs request context.
    # We can test init_solver()
    
    print("Initializing solver (this might take a moment)...")
    init_solver()
    
    from app import solver as initialized_solver
    if initialized_solver:
        print("✅ Solver initialized successfully")
    else:
        print("❌ Solver failed to initialize")
        sys.exit(1)
        
    print("Integration verification passed!")

except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error during verification: {e}")
    sys.exit(1)
