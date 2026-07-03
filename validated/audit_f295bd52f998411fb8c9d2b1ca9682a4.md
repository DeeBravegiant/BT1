### Title
`NodeDelegator` Cannot Call `setClaimerFor` on EigenLayer `RewardsCoordinator`, Permanently Freezing Unclaimed Yield When Paused - (File: contracts/NodeDelegator.sol)

### Summary
`NodeDelegator` is the earner in EigenLayer's `RewardsCoordinator`. It exposes `processClaim` to collect restaking rewards, but never exposes `setClaimerFor`. Because `processClaim` is gated by `whenNotPaused`, and no alternative claimer can ever be designated, any pause of the `NodeDelegator` permanently blocks EigenLayer reward collection with no recovery path short of unpausing.

### Finding Description
`NodeDelegator.processClaim` calls `IRewardsCoordinator.processClaim` to collect EigenLayer restaking rewards:

```solidity
function processClaim(IRewardsCoordinator.RewardsMerkleClaim calldata claim)
    external
    nonReentrant
    onlyLRTOperator
    whenNotPaused
{
    IRewardsCoordinator(lrtConfig.rewardsCoordinator()).processClaim(claim, lrtConfig.eigenLayerRewardReceiver());
}
``` [1](#0-0) 

EigenLayer's `RewardsCoordinator` enforces that `processClaim` is only callable by the earner itself **or** a designated claimer set via `setClaimerFor`:

> *only callable by the valid claimer, that is if `claimerFor[claim.earner]` is `address(0)` then only the earner can claim, otherwise only `claimerFor[claim.earner]` can claim the rewards.* [2](#0-1) 

The `setClaimerFor` function, which would allow the `NodeDelegator` (as earner) to designate an alternative claimer, exists in the interface: [3](#0-2) 

But **no corresponding function is exposed anywhere in `NodeDelegator`**. Since `claimerFor[NodeDelegator]` is permanently `address(0)`, the only entity that can ever call `IRewardsCoordinator.processClaim` for this earner is the `NodeDelegator` contract itself — through its own `processClaim`, which is `whenNotPaused`. [1](#0-0) 

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

When `NodeDelegator` is paused (a routine operational action, e.g., during a security incident or protocol migration), `processClaim` reverts. Because no alternative claimer can ever be set, EigenLayer restaking rewards accumulate in the `RewardsCoordinator` with no callable path to retrieve them. If the `NodeDelegator` is permanently deprecated (replaced by a new instance) while paused, those rewards are frozen indefinitely. Even in a temporary pause, the yield loss during the pause window is unrecoverable.

### Likelihood Explanation
**Low.** The `NodeDelegator` must be paused for the freeze to occur. Pausing is a privileged action, but it is a standard and expected operational event (e.g., emergency response). The protocol explicitly includes a `PAUSER_ROLE` and pause/unpause lifecycle, making this scenario realistic over the protocol's lifetime.

### Recommendation
Add a `setClaimerFor` wrapper to `NodeDelegator` callable by the LRT manager:

```solidity
function setEigenLayerClaimer(address claimer) external onlyLRTManager {
    IRewardsCoordinator(lrtConfig.rewardsCoordinator()).setClaimerFor(claimer);
}
```

This allows the protocol to designate an EOA or separate contract as the claimer, so EigenLayer rewards can be collected even when the `NodeDelegator` is paused.

### Proof of Concept
1. `NodeDelegator` is deployed; EigenLayer restaking rewards begin accruing in `RewardsCoordinator` with `NodeDelegator` as the earner and `claimerFor[NodeDelegator] == address(0)`.
2. A security incident triggers `pause()` on `NodeDelegator`.
3. The operator attempts to call `NodeDelegator.processClaim(...)` — it reverts with `Pausable: paused`.
4. No external party can call `IRewardsCoordinator.processClaim` directly for the `NodeDelegator` earner, because `claimerFor[NodeDelegator] == address(0)` means only the `NodeDelegator` itself is authorized.
5. There is no `setClaimerFor` function in `NodeDelegator` to designate an alternative claimer.
6. EigenLayer rewards are frozen for the entire duration of the pause, with no recovery path other than unpausing the contract — which may not be possible if the contract is permanently deprecated.

### Citations

**File:** contracts/NodeDelegator.sol (L202-209)
```text
    function processClaim(IRewardsCoordinator.RewardsMerkleClaim calldata claim)
        external
        nonReentrant
        onlyLRTOperator
        whenNotPaused
    {
        IRewardsCoordinator(lrtConfig.rewardsCoordinator()).processClaim(claim, lrtConfig.eigenLayerRewardReceiver());
    }
```

**File:** contracts/external/eigenlayer/interfaces/IRewardsCoordinator.sol (L439-443)
```text
     * @dev only callable by the valid claimer, that is
     * if claimerFor[claim.earner] is address(0) then only the earner can claim, otherwise only
     * claimerFor[claim.earner] can claim the rewards.
     */
    function processClaim(RewardsMerkleClaim calldata claim, address recipient) external;
```

**File:** contracts/external/eigenlayer/interfaces/IRewardsCoordinator.sol (L476-480)
```text
     * @notice Sets the address of the entity that can call `processClaim` on ehalf of an earner
     * @param claimer The address of the entity that can call `processClaim` on behalf of the earner
     * @dev Assumes msg.sender is the earner
     */
    function setClaimerFor(address claimer) external;
```
