Q3106: callback underpayment acceptance in transient reentrancy guard when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while protocol and admin fee accumulators are already non-zero, so that the callback debt check is reachable but can be satisfied with the wrong token amount, wrong side, or stale context along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that reach the callback with a public flow that causes the pool to accept settlement inconsistent with the signed deltas. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: The callback path must reject every underpayment, side mismatch, or stale-context payment before state is finalized. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: High direct loss from underpaying the pool or reusing stale settlement authority.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
