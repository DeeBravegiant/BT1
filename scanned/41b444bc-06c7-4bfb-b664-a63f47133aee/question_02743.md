Q2743: state-view divergence in liquidity burn path when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::removeLiquidity` with repeated exact-output attempts that accumulate rounding residue while the current bin is effectively one-sided on one token leg, so that public EXTSLOAD-derived state no longer matches the pool layout that live integrations rely on along `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner`, corrupting the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers? A public LP can repeatedly burn from edge bins, paused pools, and partially empty positions to stress floor rounding and minimal-liquidity rules. Move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::removeLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::removeLiquidity
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner` in a live public flow and show that move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read. The exact value at risk is the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Invariant to test: Every public view that powers routing or risk checks must decode exactly the same state that production logic uses. The concrete assertion should cover the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Expected Immunefi impact: Medium integration-driven loss-making execution or broken pool UX above Sherlock thresholds.
- Fast validation: Model repeated mint-burn cycles and assert no LP can withdraw more native token value than their proportional scaled claim even when the pool is paused.
