Q3437: dust-share asymmetry in EXTSLOAD state decoding when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol` with public router settlement that reaches the pool callback path while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer`, corrupting packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations? An attacker can move live pool state with a public swap just before an integration or quoter reads EXTSLOAD-decoded state. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/libraries/PoolStateLibrary.sol::{_slot0,_slot1,_slot2,_binState,_positionBinShares}
- Entrypoint: metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Assert every public state-view decode matches direct storage expectations across boundary bins, negative indices, and non-zero fee accumulators.
