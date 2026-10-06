"""Test-only parser replacement that reports readiness then never finishes."""

import sys
import time

sys.stdin.buffer.read()
sys.stdout.buffer.write(b"STARTED\n")
sys.stdout.buffer.flush()
time.sleep(60)
