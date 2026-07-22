Q2685: callback underpayment acceptance in liquidity burn path when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::removeLiquidity` with public router settlement that reaches the pool callback path while the active position sits exactly at the start of a bin segment, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner`, corrupting the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers? A public LP can repeatedly burn from edge bins, paused pools, and partially empty positions to stress floor rounding and minimal-liquidity rules. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::removeLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::removeLiquidity
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Model repeated mint-burn cycles and assert no LP can withdraw more native token value than their proportional scaled claim even when the pool is paused.
