Q1522: state-view divergence in buy-token0 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while the active position sits exactly at the start of a bin segment, so that public EXTSLOAD-derived state no longer matches the pool layout that live integrations rely on along `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output`, corrupting the analytical target position, the implied average price, and the token0 amount the pool releases? A trader can force the library into extreme but valid public states by choosing input size, direction, and thin-bin inventory distribution. Move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForBuyToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output` in a live public flow and show that move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read. The exact value at risk is the analytical target position, the implied average price, and the token0 amount the pool releases.
- Invariant to test: Every public view that powers routing or risk checks must decode exactly the same state that production logic uses. The concrete assertion should cover the analytical target position, the implied average price, and the token0 amount the pool releases.
- Expected Immunefi impact: Medium integration-driven loss-making execution or broken pool UX above Sherlock thresholds.
- Fast validation: Cross-check the analytical target against a slower stepwise reference model and assert the pool never outputs more token0 than the discrete bin curve permits.
