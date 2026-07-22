Q713: callback underpayment acceptance in bid-ask to mid conversion when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `amountSpecified` near sign, zero, and `int128` edge cases while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path`, corrupting `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them? The attacker cannot forge oracle admin data, but can choose swap timing and direction exactly when the live quote is near a boundary that makes ceil/floor matter. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::midAndSpreadFeeX64FromBidAsk
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Drive the pool with edge-case bid/ask pairs from a test price provider and compare the implied mid/baseFee against downstream token movement and fee accounting.
