Q3752: scaled-native mismatch in depth and quote lens when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}` with timing around a just-moved cursor or a just-paused pool while protocol and admin fee accumulators are already non-zero, so that scaled internal accounting and native ERC20 transfer amounts drift apart under a reachable decimal or conversion edge case along `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators`, corrupting quoted available depth, distance from provided price, and any integration decision taken from those values? This is a public medium-severity surface where a user can front-run a consumer that trusts the lens too closely before executing a real swap. Choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::getLiquidityDepth and distanceFromProvidedPriceX64
- Entrypoint: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators` in a live public flow and show that choose a legitimate token-decimal combination and public action size that forces native conversion to disagree with scaled conservation. The exact value at risk is quoted available depth, distance from provided price, and any integration decision taken from those values.
- Invariant to test: Native token transfers must match scaled state deltas after applying the documented multiplier and rounding rules. The concrete assertion should cover quoted available depth, distance from provided price, and any integration decision taken from those values.
- Expected Immunefi impact: High direct loss of principal or pool insolvency in standard ERC20 pools.
- Fast validation: Compare lens outputs against an actual immediately-following pool execution and flag any divergence large enough to induce a loss-making trade above contest thresholds.
