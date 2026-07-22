Q3640: rounding desynchronization in depth and quote lens when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}` with timing around a just-moved cursor or a just-paused pool while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators`, corrupting quoted available depth, distance from provided price, and any integration decision taken from those values? This is a public medium-severity surface where a user can front-run a consumer that trusts the lens too closely before executing a real swap. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::getLiquidityDepth and distanceFromProvidedPriceX64
- Entrypoint: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is quoted available depth, distance from provided price, and any integration decision taken from those values.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover quoted available depth, distance from provided price, and any integration decision taken from those values.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Compare lens outputs against an actual immediately-following pool execution and flag any divergence large enough to induce a loss-making trade above contest thresholds.
