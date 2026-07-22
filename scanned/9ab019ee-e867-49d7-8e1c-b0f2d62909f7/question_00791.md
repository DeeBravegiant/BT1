Q791: transient-state reuse in bid-ask to mid conversion when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with repeated exact-output attempts that accumulate rounding residue while protocol and admin fee accumulators are already non-zero, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path`, corrupting `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them? The attacker cannot forge oracle admin data, but can choose swap timing and direction exactly when the live quote is near a boundary that makes ceil/floor matter. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::midAndSpreadFeeX64FromBidAsk
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Drive the pool with edge-case bid/ask pairs from a test price provider and compare the implied mid/baseFee against downstream token movement and fee accounting.
