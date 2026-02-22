
import unittest
from unittest.mock import MagicMock, patch
import json
import sqlite3
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, init_solver
import config

class TestTicketPersistence(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        self.db_path = config.DATABASE_PATH
        
        # Mock the solver to avoid loading heavy models
        self.mock_solver = MagicMock()
        self.mock_solver.solve.return_value = {
            'success': True,
            'solution': 'Mock solution',
            'confidence': 0.95,
            'method': 'mock',
            'attempts': 1,
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
        
        # Patch the global solver in app
        import app as app_module
        app_module.solver = self.mock_solver
        app_module.automation_specialist = MagicMock() # Mock automation too

    def test_ticket_creation_persistence(self):
        # Simulate login as admin (ID 1)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True

        # Send predict request
        payload = {
            'subject': 'Integration Test Ticket',
            'body': 'Testing if this ticket is saved to DB.'
        }
        
        response = self.client.post('/predict', 
                                  data=json.dumps(payload),
                                  content_type='application/json')
        
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
        self.assertEqual(row[1], 'Integration Test Ticket')
        self.assertEqual(str(row[2]), '1') # User ID should be 1
        
        print(f"✅ Ticket {ticket_id} successfully verified in DB with user_id=1")

if __name__ == '__main__':
    unittest.main()
