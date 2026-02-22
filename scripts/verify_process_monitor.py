
import sys
import os
import time
import json
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from src.process_monitor import ProcessMonitor

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class MockReporter:
    def receive_metrics(self, metrics):
        print("\n" + "="*50)
        print("METRICS RECEIVED")
        print("="*50)
        print(json.dumps(metrics, indent=2))
        print("="*50 + "\n")

def run_verification():
    print("Initializing ProcessMonitor...")
    
    # Create DB if not exists (for clean test) or use existing
    db_path = config.DATABASE_PATH
    
    reporter = MockReporter()
    monitor = ProcessMonitor(
        db_path=db_path, 
        status_reporter=reporter, 
        check_interval_seconds=5 # Fast interval for testing
    )
    
    print("Starting Monitor Thread...")
    monitor.start()
    
    # Let it run for a cycle
    print("Waiting for monitor cycle...")
    time.sleep(7)
    
    print("Stopping Monitor...")
    monitor.stop()
    print("Monitor Stopped.")

if __name__ == "__main__":
    run_verification()
