Q2970: cross-bin overstatement in transient reentrancy guard when the active position sits exactly at the end of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price while the active position sits exactly at the end of a bin segment, so that crossing logic counts output from one bin while charging input as if a different terminal position had been reached along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: `zeroForOne`, `recipient`, and `priceLimitX64` near the live marginal price
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: For every crossed bin, input charged and output granted must come from the same final per-bin state transition. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: Critical direct loss if a trader can repeatedly extract more output than bin reserves allow.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
