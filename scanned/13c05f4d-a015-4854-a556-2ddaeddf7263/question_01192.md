Q1192: transient-state reuse in price-limit interpolation when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with timing around a just-moved cursor or a just-paused pool while protocol and admin fee accumulators are already non-zero, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice`, corrupting the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution? This is the exact branch that decides whether a public swap stops inside the current bin or crosses farther than the user-supplied limit intended. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::calculatePriceAtBinPosition and calculateBinPositionAtPrice
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Use a deterministic bin harness with fixed lower/upper prices and assert the terminal position chosen from the limit matches the position implied by the output actually granted.
