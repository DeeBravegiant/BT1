Q2147: scaled-native mismatch in liquidity mint path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with a thin-bin state prepared by one or two public precursor swaps while protocol and admin fee accumulators are already non-zero, so that scaled internal accounting and native ERC20 transfer amounts drift apart under a reachable decimal or conversion edge case along `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback`, corrupting `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer? The attacker controls the share vector, owner/salt choice, and the timing of add-liquidity relative to the active cursor and existing LP balances. Choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::addLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: a thin-bin state prepared by one or two public precursor swaps
- Exploit idea: Reach `addLiquidity -> LiquidityLib.addLiquidity -> positionBinShares/binTotalShares/binTotals update -> modify-liquidity callback` in a live public flow and show that choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation. The exact value at risk is `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Invariant to test: Native token transfers must match scaled state deltas after applying the documented multiplier and rounding rules. The concrete assertion should cover `positionBinShares`, `binTotalShares`, `binTotals`, and the native token amounts pulled from the payer.
- Expected Immunefi impact: High direct loss of principal or pool insolvency in standard ERC20 pools.
- Fast validation: Exercise one-sided and active-bin mints with repeated public deposits and assert scaled balances, total shares, and callback token pulls remain mutually consistent.
