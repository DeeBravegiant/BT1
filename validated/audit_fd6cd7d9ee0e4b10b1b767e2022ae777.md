Looking at the external report's vulnerability class — **replay of a one-time-use authorization to corrupt a pending request's state** — I need to find an analog in NEAR MPC where an attacker can replay a valid authorization artifact to fulfill a different pending request with incorrect data.

Let me examine the `respond_verify_foreign_tx` function and how it validates the response payload.