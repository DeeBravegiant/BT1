Q1329: scaled-native mismatch in buy-token0 analytical target when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `amountSpecified` near sign, zero, and `int128` edge cases while the active position sits exactly at the end of a bin segment, so that scaled internal accounting and native ERC20 transfer amounts drift apart under a reachable decimal or conversion edge case along `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output`, corrupting the analytical target position, the implied average price, and the token0 amount the pool releases? A trader can force the library into extreme but valid public states by choosing input size, direction, and thin-bin inventory distribution. Choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForBuyToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `swap exact-input or exact-output zeroForOne=false path -> analytical target computation for token0 output` in a live public flow and show that choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation. The exact value at risk is the analytical target position, the implied average price, and the token0 amount the pool releases.
- Invariant to test: Native token transfers must match scaled state deltas after applying the documented multiplier and rounding rules. The concrete assertion should cover the analytical target position, the implied average price, and the token0 amount the pool releases.
- Expected Immunefi impact: High direct loss of principal or pool insolvency in standard ERC20 pools.
- Fast validation: Cross-check the analytical target against a slower stepwise reference model and assert the pool never outputs more token0 than the discrete bin curve permits.
