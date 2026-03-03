import sys
import os
import config
from src.problem_solver_fixed import ProblemSolver

class MockTriage:
    def predict(self, subject, body, retrieve_answer=True):
        return {
            'type': 'Incident', 'type_confidence': 0.9,
            'priority': 'High', 'priority_confidence': 0.9,
            'queue': 'Technical Support', 'queue_confidence': 0.9,
            'tags': [{'tag': 'network'}, {'tag': 'access'}],
            'answer': 'No known KB answer.',
            'answer_source': {'similarity': 0.2}
        }

if __name__ == "__main__":
    print("Initializing ProblemSolver with MockTriage and Serper enabled...")
    solver = ProblemSolver(triage_specialist=MockTriage(), enable_web_search=True)
    
    print("\nRunning solve()...")
    result = solver.solve(
        subject="Error 403 on production server",
        body="Getting 403 forbidden randomly on enterprise server port 8080. It happened after latest release.",
        ticket_id="TICKET-001"
    )
    
    print("\n--- RESULTS ---")
    print("SUCCESS:", result.get('success'))
    print("SOLUTION:\n", result.get('solution'))
