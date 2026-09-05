#!/usr/bin/env python3
"""Run every conformance suite."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ["test_matrix.py", "test_sdk.py", "test_render.py", "test_analyze.py", "test_docs.py"]

failed = []
for suite in SUITES:
    print(f"\n{'=' * 60}\n{suite}\n{'=' * 60}")
    if subprocess.run([sys.executable, str(HERE / suite)]).returncode != 0:
        failed.append(suite)

print()
if failed:
    print(f"FAILED: {', '.join(failed)}")
    sys.exit(1)
print("all suites passed")
