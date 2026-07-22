Q2350: state-view divergence in liquidity mint path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with share vectors concentrated around the active bin and its neighbors while protocol and admin fee accumulators are already non-zero, so that public EXTSLOAD-derived state no longer matches the pool layout that live integrations rely on along `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback`, corrupting `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer? The attacker controls the share vector, owner/salt choice, and the timing of add-liquidity relative to the active cursor and existing LP balances. Move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::addLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: share vectors concentrated around the active bin and its neighbors
- Exploit idea: Reach `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback` in a live public flow and show that move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read. The exact value at risk is `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Invariant to test: Every public view that powers routing or risk checks must decode exactly the same state that production logic uses. The concrete assertion should cover `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Expected Immunefi impact: Medium integration-driven loss-making execution or broken pool UX above Sherlock thresholds.
- Fast validation: Exercise one-sided and active-bin mints with repeated public deposits and assert scaled balances, total shares, and callback token pulls remain mutually consistent.
