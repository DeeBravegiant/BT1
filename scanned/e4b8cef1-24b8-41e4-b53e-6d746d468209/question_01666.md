Q1666: price-limit bypass in buy-token1 analytical target when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while protocol and admin fee accumulators are already non-zero, so that a public user-supplied price limit is accepted syntactically but not enforced at the exact point the payout is decided along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: The pool must never settle output at a worse marginal price than the user-specified reachable limit. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: High direct user loss from bad-price execution or over-delivery past the allowed limit.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
