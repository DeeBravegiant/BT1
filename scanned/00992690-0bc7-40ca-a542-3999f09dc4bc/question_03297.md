Q3297: fee-split drift in EXTSLOAD state decoding when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol` with `amountSpecified` near sign, zero, and `int128` edge cases while the current bin is effectively one-sided on one token leg, so that spread or notional fees are computed from one representation while balances are updated in another along `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer`, corrupting packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations? An attacker can move live pool state with a public swap just before an integration or quoter reads EXTSLOAD-decoded state. Accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or LP fee ownership.

Target
- File/function: metric-core/contracts/libraries/PoolStateLibrary.sol::{_slot0,_slot1,_slot2,_binState,_positionBinShares}
- Entrypoint: metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol
- Attacker controls: `amountSpecified` near sign, zero, and `int128` edge cases
- Exploit idea: Reach `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer` in a live public flow and show that accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or lp fee ownership. The exact value at risk is packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Invariant to test: Every unit of spread/notional fee must be accounted for exactly once between LPs, protocol, admin, and trader settlement. The concrete assertion should cover packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Expected Immunefi impact: Medium/High protocol-fee loss or LP-fund leakage above Sherlock thresholds.
- Fast validation: Assert every public state-view decode matches direct storage expectations across boundary bins, negative indices, and non-zero fee accumulators.
