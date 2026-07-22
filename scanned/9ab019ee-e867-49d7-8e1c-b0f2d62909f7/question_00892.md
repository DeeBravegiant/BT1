Q892: fee-split drift in price-limit interpolation when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with standard-token decimal asymmetry such as 6/18 or 6/6 pairs while the active position sits exactly at the end of a bin segment, so that spread or notional fees are computed from one representation while balances are updated in another along `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice`, corrupting the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution? This is the exact branch that decides whether a public swap stops inside the current bin or crosses farther than the user-supplied limit intended. Accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or LP fee ownership.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::calculatePriceAtBinPosition and calculateBinPositionAtPrice
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: standard-token decimal asymmetry such as 6/18 or 6/6 pairs
- Exploit idea: Reach `swap -> analytical/bin-step target selection -> calculatePriceAtBinPosition / calculateBinPositionAtPrice` in a live public flow and show that accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or lp fee ownership. The exact value at risk is the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Invariant to test: Every unit of spread/notional fee must be accounted for exactly once between LPs, protocol, admin, and trader settlement. The concrete assertion should cover the terminal bin position, the effective stop price, and the amount paid out before the price limit is supposed to halt execution.
- Expected Immunefi impact: Medium/High protocol-fee loss or LP-fund leakage above Sherlock thresholds.
- Fast validation: Use a deterministic bin harness with fixed lower/upper prices and assert the terminal position chosen from the limit matches the position implied by the output actually granted.
