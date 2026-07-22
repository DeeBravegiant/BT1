Q293: callback underpayment acceptance in pool swap settlement when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public router settlement that reaches the pool callback path while the active position sits exactly at the end of a bin segment, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta`, corrupting scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer? This path is the load-bearing spot where trader-controlled direction, limits, and callback settlement meet the bin math. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/MetricOmmPool.sol::swap -> _executeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Build a Foundry pool harness that snapshots slot0/slot1 before and after the trade and asserts pool balances plus callback deltas match the returned swap deltas.
