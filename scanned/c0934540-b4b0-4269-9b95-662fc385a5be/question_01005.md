Q1005: dust-share asymmetry in price-limit interpolation when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public router settlement that reaches the pool callback path while the active position sits exactly at the start of a bin segment, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice`, corrupting the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution? This is the exact branch that decides whether a public swap stops inside the current bin or crosses farther than the user-supplied limit intended. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::calculatePriceAtBinPosition and calculateBinPositionAtPrice
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Use a deterministic bin harness with fixed lower/upper prices and assert the terminal position chosen from the limit matches the position implied by the output actually granted.
