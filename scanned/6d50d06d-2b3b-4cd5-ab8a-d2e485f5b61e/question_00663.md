Q663: paused-withdraw inconsistency in bid-ask to mid conversion when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with repeated exact-output attempts that accumulate rounding residue while the current bin is effectively one-sided on one token leg, so that the contract intentionally permits withdrawals while paused, but some reachable branch still depends on active-swap assumptions along `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path`, corrupting `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them? The attacker cannot forge oracle admin data, but can choose swap timing and direction exactly when the live quote is near a boundary that makes ceil/floor matter. Pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::midAndSpreadFeeX64FromBidAsk
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `swap -> _getBidAndAskPriceX64 -> midAndSpreadFeeX64FromBidAsk -> internal swap path` in a live public flow and show that pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions. The exact value at risk is `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Invariant to test: A paused pool must remain solvent and withdrawable for honest LPs without reusing swap-only assumptions. The concrete assertion should cover `midPriceX64`, `baseFeeX64`, and every later gross-input or fee computation derived from them.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds above contest thresholds.
- Fast validation: Drive the pool with edge-case bid/ask pairs from a test price provider and compare the implied mid/baseFee against downstream token movement and fee accounting.
