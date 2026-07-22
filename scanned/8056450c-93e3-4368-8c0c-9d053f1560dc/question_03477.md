Q3477: paused-withdraw inconsistency in EXTSLOAD state decoding when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol` with public router settlement that reaches the pool callback path while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that the contract intentionally permits withdrawals while paused, but some reachable branch still depends on active-swap assumptions along `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer`, corrupting packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations? An attacker can move live pool state with a public swap just before an integration or quoter reads EXTSLOAD-decoded state. Pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions.

Target
- File/function: metric-core/contracts/libraries/PoolStateLibrary.sol::{_slot0,_slot1,_slot2,_binState,_positionBinShares}
- Entrypoint: metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer` in a live public flow and show that pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions. The exact value at risk is packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Invariant to test: A paused pool must remain solvent and withdrawable for honest LPs without reusing swap-only assumptions. The concrete assertion should cover packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds above contest thresholds.
- Fast validation: Assert every public state-view decode matches direct storage expectations across boundary bins, negative indices, and non-zero fee accumulators.
