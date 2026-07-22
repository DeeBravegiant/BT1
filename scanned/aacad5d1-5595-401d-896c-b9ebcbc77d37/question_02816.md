Q2816: rounding desynchronization in transient reentrancy guard when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with timing around a just-moved cursor or a just-paused pool while the active position sits exactly at the end of a bin segment, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
