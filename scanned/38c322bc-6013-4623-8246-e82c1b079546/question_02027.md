Q2027: rounding desynchronization in liquidity mint path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with a thin-bin state prepared by one or two public precursor swaps while protocol and admin fee accumulators are already non-zero, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback`, corrupting `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer? The attacker controls the share vector, owner/salt choice, and the timing of add-liquidity relative to the active cursor and existing LP balances. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::addLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: a thin-bin state prepared by one or two public precursor swaps
- Exploit idea: Reach `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Exercise one-sided and active-bin mints with repeated public deposits and assert scaled balances, total shares, and callback token pulls remain mutually consistent.
