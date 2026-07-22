Q2799: transient-state reuse in liquidity burn path when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::removeLiquidity` with repeated exact-output attempts that accumulate rounding residue while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner`, corrupting the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers? A public LP can repeatedly burn from edge bins, paused pools, and partially empty positions to stress floor rounding and minimal-liquidity rules. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::removeLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::removeLiquidity
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Model repeated mint-burn cycles and assert no LP can withdraw more native token value than their proportional scaled claim even when the pool is paused.
