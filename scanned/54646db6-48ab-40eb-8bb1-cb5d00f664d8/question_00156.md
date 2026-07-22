Q156: scaled-native mismatch in pool swap settlement when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with standard-token decimal asymmetry such as 6/18 or 6/6 pairs while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that scaled internal accounting and native ERC20 transfer amounts drift apart under a reachable decimal or conversion edge case along `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta`, corrupting scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer? This path is the load-bearing spot where trader-controlled direction, limits, and callback settlement meet the bin math. Choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation.

Target
- File/function: metric-core/contracts/MetricOmmPool.sol::swap -> _executeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: standard-token decimal asymmetry such as 6/18 or 6/6 pairs
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> SwapMath.midAndSpreadFeeX64FromBidAsk -> _executeSwap -> metricOmmSwapCallback -> IncorrectDelta` in a live public flow and show that choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation. The exact value at risk is scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Invariant to test: Native token transfers must match scaled state deltas after applying the documented multiplier and rounding rules. The concrete assertion should cover scaled bin balances, `protocolFeeAmount`, `curBinIdx`, `curPosInBin`, and the output token transfer.
- Expected Immunefi impact: High direct loss of principal or pool insolvency in standard ERC20 pools.
- Fast validation: Build a Foundry pool harness that snapshots slot0/slot1 before and after the trade and asserts pool balances plus callback deltas match the returned swap deltas.
