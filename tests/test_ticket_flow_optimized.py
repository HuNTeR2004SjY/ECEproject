
import unittest
from unittest.mock import MagicMock, patch
import json
import sqlite3
import os
import sys

# MOCK HEAVY MODULES BEFORE IMPORTING APP
sys.modules['problem_solver_fixed'] = MagicMock()
sys.modules['inference_service_full'] = MagicMock()
sys.modules['automation_specialist'] = MagicMock()

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app
import config

class TestTicketPersistence(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        self.db_path = config.DATABASE_PATH
        
        # Configure the mocked solver inside app
        # Since we mocked the module, the class instantiation inside app.py returned a mock
        # But app.py does: solver = ProblemSolver(...) inside init_solver()
        # So when init_solver is called, it returns a mock.
        # But wait, app.py imports the CLASS. 
        # specifically: `from problem_solver_fixed import ProblemSolver`
        # So `sys.modules['problem_solver_fixed'].ProblemSolver` is the class mock.
        
        # We need to ensure init_solver works or we manually set app.solver
        import app as app_module
        
        # Mock the solver instance that will be created
        self.mock_solver_instance = MagicMock()
        self.mock_solver_instance.solve.return_value = {
            'success': True,
            'solution': 'Mock solution',
            'confidence': 0.95,
            'method': 'mock',
            'triage': {
                'type': 'Incident', 
                'type_confidence': 0.9,
                'priority': 'High',
                'priority_confidence': 0.8,
                'queue': 'Support',
                'queue_confidence': 0.8,
                'tags': [{'tag': 'Test', 'confidence': 0.9}]
            }
        }
        
        # Manually set the global solver variable in app module to avoid calling init_solver
        app_module.solver = self.mock_solver_instance

    def test_ticket_creation_persistence(self):
        # Simulate login as admin (ID 1)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True

        # Send predict request
        payload = {
            'subject': 'Optimized Test Ticket',
            'body': 'Testing WITHOUT loading models.'
        }
        
        response = self.client.post('/predict', 
                                  data=json.dumps(payload),
                                  content_type='application/json')
        
        if response.status_code != 200:
            print(f"Error Response: {response.data}")
            
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        ticket_id = data['ticket_id']
        print(f"Generated Ticket ID: {ticket_id}")
        
        # Verify in Database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, subject, user_id FROM classified_tickets WHERE id = ?", (ticket_id,))
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 'Optimized Test Ticket')
        self.assertEqual(str(row[2]), '1') # User ID should be 1
        
        print(f"Ticket {ticket_id} successfully verified in DB with user_id=1")

if __name__ == '__main__':
    unittest.main()
