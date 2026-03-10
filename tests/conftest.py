"""
conftest.py — Session-scoped fixtures for the ECE test suite.

Fixtures provided:
  - flask_server:          Starts Flask on host:5000 in a daemon thread.
  - test_summary_reporter: Prints a Test ID | Actual Value | Pass/Fail table.
"""

import os
import sys
import time
import threading

import pytest
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

# ─────────────────────────────────────────────────────────────────────────────
# Storage for the end-of-session summary
# ─────────────────────────────────────────────────────────────────────────────
_test_results: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Hook: capture actual_value + pass/fail per test
# ─────────────────────────────────────────────────────────────────────────────
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        # Tests store their actual value as `request.node.actual_value`
        actual = getattr(item, "actual_value", "N/A")
        _test_results[item.name] = {
            "status": "PASS" if report.passed else ("SKIP" if report.skipped else "FAIL"),
            "actual_value": actual,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Flask server fixture
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def flask_server():
    """
    Starts the Flask development server in a daemon thread so that system
    tests (ST-*) can hit http://localhost:5000.
    The server stops automatically when the pytest session ends (daemon thread).
    """
    import config
    from app import app, init_solver

    # Pre-init so the background thread doesn't race against model loading
    with app.app_context():
        try:
            init_solver()
        except Exception as e:
            print(f"[conftest] init_solver warning: {e}")

    def _run():
        # use_reloader=False is mandatory inside a thread
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Poll until the server is responsive (max 60 s)
    for _ in range(120):
        try:
            r = requests.get("http://localhost:5000/", timeout=1, allow_redirects=False)
            if r.status_code in (200, 302):
                print("\n[conftest] Flask server is ready.")
                break
        except requests.exceptions.ConnectionError:
            time.sleep(0.5)
    else:
        print("\n[conftest] WARNING: Flask server may not be ready — system tests might fail.")

    yield  # run tests

    print("\n[conftest] Flask server fixture teardown (daemon thread will exit with pytest).")


# ─────────────────────────────────────────────────────────────────────────────
# Summary reporter fixture
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def test_summary_reporter(request):
    """Prints a formatted summary table after all tests complete."""
    yield

    rows = _test_results
    if not rows:
        return

    col1, col2, col3 = 35, 35, 8
    sep = "-" * (col1 + col2 + col3 + 6)
    print("\n" + "=" * (col1 + col2 + col3 + 6))
    print(f"{'TEST ID':<{col1}} | {'ACTUAL VALUE':<{col2}} | {'STATUS':<{col3}}")
    print(sep)
    for test_id, data in rows.items():
        actual = str(data["actual_value"])[:col2]
        status = data["status"]
        print(f"{test_id:<{col1}} | {actual:<{col2}} | {status:<{col3}}")
    print("=" * (col1 + col2 + col3 + 6) + "\n")
