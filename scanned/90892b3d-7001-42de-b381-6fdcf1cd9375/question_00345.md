Q345: state-view divergence in pool swap settlement when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `amountSpecified` near sign, zero, and `int128` edge cases while protocol and admin fee accumulators are already non-zero, so that public EXTSLOAD-derived state no longer matches the pool layout that live integrations rely on along `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta`, corrupting scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer? This path is the load-bearing spot where trader-controlled direction, limits, and callback settlement meet the bin math. Move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read.

Target
- File/function: metric-core/contracts/MetricOmmPool.sol::swap -> _executeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta` in a live public flow and show that move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read. The exact value at risk is scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Invariant to test: Every public view that powers routing or risk checks must decode exactly the same state that production logic uses. The concrete assertion should cover scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Expected Immunefi impact: Medium integration-driven loss-making execution or broken pool UX above Sherlock thresholds.
- Fast validation: Build a Foundry pool harness that snapshots slot0/slot1 before and after the trade and asserts pool balances plus callback deltas match the returned swap deltas.
