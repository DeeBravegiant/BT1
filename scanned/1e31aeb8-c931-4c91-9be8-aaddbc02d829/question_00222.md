Q222: dust-share asymmetry in pool swap settlement when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with share vectors concentrated around the active bin and its neighbors while the current bin is effectively one-sided on one token leg, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta`, corrupting scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer? This path is the load-bearing spot where trader-controlled direction, limits, and callback settlement meet the bin math. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/MetricOmmPool.sol::swap -> _executeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: share vectors concentrated around the active bin and its neighbors
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Build a Foundry pool harness that snapshots slot0/slot1 before and after the trade and asserts pool balances plus callback deltas match the returned swap deltas.
