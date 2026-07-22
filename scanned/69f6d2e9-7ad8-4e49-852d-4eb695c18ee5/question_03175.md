Q3175: transient-state reuse in transient reentrancy guard when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with repeated exact-output attempts that accumulate rounding residue while the active position sits exactly at the end of a bin segment, so that a revert or nested public call leaves transient state reusable by the next reachable action in the same transaction along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that cause a mid-flight revert and immediately invoke another public action that should have seen a clean transient context. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: No reachable revert path may leave an action id, callback authority, or lock flag active for the next user-controlled step. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: High direct loss if stale transient authority can redirect payment or bypass action isolation.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
