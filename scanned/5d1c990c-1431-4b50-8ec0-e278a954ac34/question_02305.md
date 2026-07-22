Q2305: callback underpayment acceptance in liquidity mint path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with `amountSpecified` near sign, zero, and `int128` edge cases while protocol and admin fee accumulators are already non-zero, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback`, corrupting `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer? The attacker controls the share vector, owner/salt choice, and the timing of add-liquidity relative to the active cursor and existing LP balances. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::addLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Exercise one-sided and active-bin mints with repeated public deposits and assert scaled balances, total shares, and callback token pulls remain mutually consistent.
