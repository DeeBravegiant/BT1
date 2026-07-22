Q3031: dust-share asymmetry in transient reentrancy guard when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with repeated exact-output attempts that accumulate rounding residue while protocol and admin fee accumulators are already non-zero, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `public pool action -> transient lock set -> internal execution -> transient lock clear`, corrupting the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path? The attacker can only use public pool and router entrypoints, but can chain them in one transaction and force reverts through callbacks or extension hooks. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/utils/MetricReentrancyGuardTransient.sol::_nonReentrantBefore/_nonReentrantAfter
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `public pool action -> transient lock set -> internal execution -> transient lock clear` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover the active action id, callback authority, and the guarantee that no stale transient state survives a revert or nested user-triggered path.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Build nested multicall and callback scenarios that revert at different depths and assert the transient lock and action id always reset to the safe empty state.
