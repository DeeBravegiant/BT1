Q3261: price-limit bypass in EXTSLOAD state decoding when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol` with public router settlement that reaches the pool callback path while the current bin is effectively one-sided on one token leg, so that a public user-supplied price limit is accepted syntactically but not enforced at the exact point the payout is decided along `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer`, corrupting packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations? An attacker can move live pool state with a public swap just before an integration or quoter reads EXTSLOAD-decoded state. Use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts.

Target
- File/function: metric-core/contracts/libraries/PoolStateLibrary.sol::{_slot0,_slot1,_slot2,_binState,_positionBinShares}
- Entrypoint: metric-periphery/contracts/common/MetricOmmPoolStateView.sol and metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `public view helper -> PoolStateLibrary slot calculation -> extsload decode -> quoter/depth consumer` in a live public flow and show that use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts. The exact value at risk is packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Invariant to test: The pool must never settle output at a worse marginal price than the user-specified reachable limit. The concrete assertion should cover packed slot0 fields, bin balances, notional-fee accumulators, and position-share reads consumed by live integrations.
- Expected Immunefi impact: High direct user loss from bad-price execution or over-delivery past the allowed limit.
- Fast validation: Assert every public state-view decode matches direct storage expectations across boundary bins, negative indices, and non-zero fee accumulators.
