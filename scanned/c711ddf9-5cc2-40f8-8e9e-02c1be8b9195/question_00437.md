Q437: rounding desynchronization in bid-ask to mid conversion when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public router settlement that reaches the pool callback path while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path`, corrupting `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them? The attacker cannot forge oracle admin data, but can choose swap timing and direction exactly when the live quote is near a boundary that makes ceil/floor matter. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::midAndSpreadFeeX64FromBidAsk
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Drive the pool with edge-case bid/ask pairs from a test price provider and compare the implied mid/baseFee against downstream token movement and fee accounting.
