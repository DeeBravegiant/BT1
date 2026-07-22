Q376: transient-state reuse in pool swap settlement when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with timing around a just-moved cursor or a just-paused pool while the active position sits exactly at the end of a bin segment, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta`, corrupting scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer? This path is the load-bearing spot where trader-controlled direction, limits, and callback settlement meet the bin math. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-core/contracts/MetricOmmPool.sol::swap -> _executeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Build a Foundry pool harness that snapshots slot0/slot1 before and after the trade and asserts pool balances plus callback deltas match the returned swap deltas.
