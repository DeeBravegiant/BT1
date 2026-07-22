Q3990: transient-state reuse in depth and quote lens when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}` with share vectors concentrated around the active bin and its neighbors while protocol and admin fee accumulators are already non-zero, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators`, corrupting quoted available depth, distance from provided price, and any integration decision taken from those values? This is a public medium-severity surface where a user can front-run a consumer that trusts the lens too closely before executing a real swap. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::getLiquidityDepth and distanceFromProvidedPriceX64
- Entrypoint: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}
- Attacker controls: share vectors concentrated around the active bin and its neighbors
- Exploit idea: Reach `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is quoted available depth, distance from provided price, and any integration decision taken from those values.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover quoted available depth, distance from provided price, and any integration decision taken from those values.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Compare lens outputs against an actual immediately-following pool execution and flag any divergence large enough to induce a loss-making trade above contest thresholds.
