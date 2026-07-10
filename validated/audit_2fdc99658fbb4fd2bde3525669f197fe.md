Looking at the vulnerability class from M-05 — **vote-count state updated without re-running the threshold check** — I need to find an analog in NEAR MPC where a state change increases effective vote support (or decreases the required threshold) without re-evaluating whether the transition condition is now met.

Let me examine the vote-filtering logic that runs on state transitions.