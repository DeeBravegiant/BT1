Looking at the NEAR MPC codebase, I need to find an analog to the UFarmPool slippage issue — specifically, a **request/accounting/state corruption** vulnerability where a user's request is processed with a different output than expected, with no protection mechanism.

Let me examine the `respond_ckd` function and the CKD request lifecycle.