Q2168: cross-bin overstatement in liquidity mint path when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with timing around a just-moved cursor or a just-paused pool while the active position sits exactly at the start of a bin segment, so that crossing logic counts output from one bin while charging input as if a different terminal position had been reached along `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback`, corrupting `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer? The attacker controls the share vector, owner/salt choice, and the timing of add-liquidity relative to the active cursor and existing LP balances. Drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::addLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback` in a live public flow and show that drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state. The exact value at risk is `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Invariant to test: For every crossed bin, input charged and output granted must come from the same final per-bin state transition. The concrete assertion should cover `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Expected Immunefi impact: Critical direct loss if a trader can repeatedly extract more output than bin reserves allow.
- Fast validation: Exercise one-sided and active-bin mints with repeated public deposits and assert scaled balances, total shares, and callback token pulls remain mutually consistent.
