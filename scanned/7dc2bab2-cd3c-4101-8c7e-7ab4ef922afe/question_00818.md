Q818: rounding desynchronization in price-limit interpolation when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while the current bin is effectively one-sided on one token leg, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice`, corrupting the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution? This is the exact branch that decides whether a public swap stops inside the current bin or crosses farther than the user-supplied limit intended. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::calculatePriceAtBinPosition and calculateBinPositionAtPrice
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Use a deterministic bin harness with fixed lower/upper prices and assert the terminal position chosen from the limit matches the position implied by the output actually granted.
