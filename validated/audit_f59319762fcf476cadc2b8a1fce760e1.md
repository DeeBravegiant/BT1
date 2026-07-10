Looking at the NEAR MPC codebase for an analog to the "incorrect check logic / zero-value bypass" vulnerability class from the external report, I need to find a validation check that can be bypassed due to incorrect or missing logic under a specific state condition.

Let me examine the `respond_ckd()` function and the CKD output verification logic.