Q1881: callback underpayment acceptance in buy-token1 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `amountSpecified` near sign, zero, and `int128` edge cases while the active position sits exactly at the start of a bin segment, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
