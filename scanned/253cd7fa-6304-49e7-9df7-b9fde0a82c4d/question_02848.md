Q2848: price-limit bypass in transient reentrancy guard when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with timing around a just-moved cursor or a just-paused pool while the active position sits exactly at the start of a bin segment, so that a public user-supplied price limit is accepted syntactically but not enforced at the exact point the payout is decided along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that use a limit that should stop inside the current bin and see whether execution crosses farther before accounting halts. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: The pool must never settle output at a worse marginal price than the user-specified reachable limit. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: High direct user loss from bad-price execution or over-delivery past the allowed limit.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
