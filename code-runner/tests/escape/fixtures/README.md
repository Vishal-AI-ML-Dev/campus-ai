# Synthetic hostile fixtures (never student code)

Each program attacks the sandbox and prints `BLOCKED <check>` when the attack fails, or `ESCAPED <check>` when it
succeeds. They run ONLY through `tests/escape/test_escape_suite.py`, which refuses to start unless the host passes the
runner self-check (pinned Docker + gVisor `runsc`, systrap). They must never be run on a developer machine, on plain
Docker (`runc`) or outside the runner.
