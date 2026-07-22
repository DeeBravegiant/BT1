Q1160: state-view divergence in price-limit interpolation when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with timing around a just-moved cursor or a just-paused pool while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that public EXTSLOAD-derived state no longer matches the pool layout that live integrations rely on along `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice`, corrupting the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution? This is the exact branch that decides whether a public swap stops inside the current bin or crosses farther than the user-supplied limit intended. Move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::calculatePriceAtBinPosition and calculateBinPositionAtPrice
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice` in a live public flow and show that move the pool into a valid public state where slot decoding, key derivation, or sign handling returns a materially wrong read. The exact value at risk is the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Invariant to test: Every public view that powers routing or risk checks must decode exactly the same state that production logic uses. The concrete assertion should cover the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Expected Immunefi impact: Medium integration-driven loss-making execution or broken pool UX above Sherlock thresholds.
- Fast validation: Use a deterministic bin harness with fixed lower/upper prices and assert the terminal position chosen from the limit matches the position implied by the output actually granted.
