Q3674: price-limit bypass in depth and quote lens when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that a public user-supplied price limit is accepted syntactically but not enforced at the exact point the payout is decided along `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators`, corrupting quoted available depth, distance from provided price, and any integration decision taken from those values? This is a public medium-severity surface where a user can front-run a consumer that trusts the lens too closely before executing a real swap. Use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts.

Target
- File/function: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::getLiquidityDepth and distanceFromProvidedPriceX64
- Entrypoint: metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol::{getLiquidityDepth,distanceFromProvidedPriceX64}
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `public lens call -> PoolStateLibrary reads -> derived depth or distance values used by routers/integrators` in a live public flow and show that use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts. The exact value at risk is quoted available depth, distance from provided price, and any integration decision taken from those values.
- Invariant to test: The pool must never settle output at a worse marginal price than the user-specified reachable limit. The concrete assertion should cover quoted available depth, distance from provided price, and any integration decision taken from those values.
- Expected Immunefi impact: High direct user loss from bad-price execution or over-delivery past the allowed limit.
- Fast validation: Compare lens outputs against an actual immediately-following pool execution and flag any divergence large enough to induce a loss-making trade above contest thresholds.
