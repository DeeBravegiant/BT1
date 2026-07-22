Q1401: dust-share asymmetry in buy-token0 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `amountSpecified` near sign, zero, and `int128` edge cases while the active position sits exactly at the start of a bin segment, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output`, corrupting the analytical target position, the implied average price, and the token0 amount the pool releases? A trader can force the library into extreme but valid public states by choosing input size, direction, and thin-bin inventory distribution. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForBuyToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is the analytical target position, the implied average price, and the token0 amount the pool releases.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover the analytical target position, the implied average price, and the token0 amount the pool releases.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Cross-check the analytical target against a slower stepwise reference model and assert the pool never outputs more token0 than the discrete bin curve permits.
