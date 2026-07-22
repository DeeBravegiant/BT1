Q2507: fee-split drift in liquidity burn path when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::removeLiquidity` with a thin-bin state prepared by one or two public precursor swaps while protocol and admin fee accumulators are already non-zero, so that spread or notional fees are computed from one representation while balances are updated in another along `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner`, corrupting the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers? A public LP can repeatedly burn from edge bins, paused pools, and partially empty positions to stress floor rounding and minimal-liquidity rules. Accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or LP fee ownership.

Target
- File/function: metric-core/contracts/libraries/LiquidityLib.sol::removeLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::removeLiquidity
- Attacker controls: a thin-bin state prepared by one or two public precursor swaps
- Exploit idea: Reach `removeLiquidity -> LiquidityLib.removeLiquidity -> binTotals decrement -> native token transfers to owner` in a live public flow and show that accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or lp fee ownership. The exact value at risk is the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Invariant to test: Every unit of spread/notional fee must be accounted for exactly once between LPs, protocol, admin, and trader settlement. The concrete assertion should cover the amount removed per bin, the surviving share balance, and the relationship between scaled balances and native transfers.
- Expected Immunefi impact: Medium/High protocol-fee loss or LP-fund leakage above Sherlock thresholds.
- Fast validation: Model repeated mint-burn cycles and assert no LP can withdraw more native token value than their proportional scaled claim even when the pool is paused.
