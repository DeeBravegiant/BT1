Q2672: paused-withdraw inconsistency in liquidity burn path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::removeLiquidity` with timing around a just-moved cursor or a just-paused pool while protocol and admin fee accumulators are already non-zero, so that the contract intentionally permits withdrawals while paused, but some reachable branch still depends on active-swap assumptions along `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner`, corrupting the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers? A public LP can repeatedly burn from edge bins, paused pools, and partially empty positions to stress floor rounding and minimal-liquidity rules. Pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::removeLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::removeLiquidity
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner` in a live public flow and show that pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions. The exact value at risk is the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Invariant to test: A paused pool must remain solvent and withdrawable for honest LPs without reusing swap-only assumptions. The concrete assertion should cover the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds above contest thresholds.
- Fast validation: Model repeated mint-burn cycles and assert no LP can withdraw more native token value than their proportional scaled claim even when the pool is paused.
