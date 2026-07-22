Q1364: cross-bin overstatement in buy-token0 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with standard-token decimal asymmetry such as 6/18 or 6/6 pairs while the active position sits exactly at the start of a bin segment, so that crossing logic counts output from one bin while charging input as if a different terminal position had been reached along `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output`, corrupting the analytical target position, the implied average price, and the token0 amount the pool releases? A trader can force the library into extreme but valid public states by choosing input size, direction, and thin-bin inventory distribution. Drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForBuyToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: standard-token decimal asymmetry such as 6/18 or 6/6 pairs
- Exploit idea: Reach `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output` in a live public flow and show that drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state. The exact value at risk is the analytical target position, the implied average price, and the token0 amount the pool releases.
- Invariant to test: For every crossed bin, input charged and output granted must come from the same final per-bin state transition. The concrete assertion should cover the analytical target position, the implied average price, and the token0 amount the pool releases.
- Expected Immunefi impact: Critical direct loss if a trader can repeatedly extract more output than bin reserves allow.
- Fast validation: Cross-check the analytical target against a slower stepwise reference model and assert the pool never outputs more token0 than the discrete bin curve permits.
