Q499: fee-split drift in bid-ask to mid conversion when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with a thin-bin state prepared by one or two public precursor swaps while the current bin is effectively one-sided on one token leg, so that spread or notional fees are computed from one representation while balances are updated in another along `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path`, corrupting `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them? The attacker cannot forge oracle admin data, but can choose swap timing and direction exactly when the live quote is near a boundary that makes ceil/floor matter. Accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or LP fee ownership.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::midAndSpreadFeeX64FromBidAsk
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: a thin-bin state prepared by one or two public precursor swaps
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path` in a live public flow and show that accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or lp fee ownership. The exact value at risk is `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Invariant to test: Every unit of spread/notional fee must be accounted for exactly once between LPs, protocol, admin, and trader settlement. The concrete assertion should cover `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Expected Immunefi impact: Medium/High protocol-fee loss or LP-fund leakage above Sherlock thresholds.
- Fast validation: Drive the pool with edge-case bid/ask pairs from a test price provider and compare the implied mid/baseFee against downstream token movement and fee accounting.
