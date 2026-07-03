### Title
Owner Can Pause Vesting Claims and Drain All KERNEL Tokens, Permanently Blocking Users from Receiving Vested Rewards - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
`KernelTop100MerkleDistributor` distributes KERNEL tokens to the top-100 users under a 30-day linear vesting schedule. Both user-facing claim functions — `claim()` and `claimAndStake()` — carry the `whenNotPaused` modifier, while `pause()` is callable by the owner alone. A malicious or compromised owner can pause the contract indefinitely, blocking all vesting claims. Critically, `withdrawTokens()` carries no `whenNotPaused` guard, so the owner can drain the entire KERNEL balance from the contract while users remain locked out, permanently destroying their ability to receive vested tokens.

### Finding Description
`KernelTop100MerkleDistributor` implements a 30-day linear vesting schedule for KERNEL token rewards. Users call `claim()` or `claimAndStake()` to receive the portion of their allocation that has vested since their last claim. Both entry points are gated by `whenNotPaused`:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol:310
function claim(uint256 amount, bytes32[] calldata merkleProof)
    external nonReentrant whenNotPaused { ... }

// contracts/KERNEL/KernelTop100MerkleDistributor.sol:345
function claimAndStake(uint256 amount, bytes32[] calldata merkleProof)
    external nonReentrant whenNotPaused { ... }
```

The owner can pause the contract at any time:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol:475
function pause() external onlyOwner { _pause(); }
```

While the contract is paused, the owner can also call `withdrawTokens()`, which has no `whenNotPaused` guard:

```solidity
// contracts/KERNEL/KernelTop100MerkleDistributor.sol:461
function withdrawTokens(address _token, uint256 _amount, address _recipient)
    external onlyOwner { ... IERC20(_token).safeTransfer(_recipient, _amount); }
```

This combination allows the owner to:
1. Call `pause()` — all `claim()` / `claimAndStake()` calls revert.
2. Call `withdrawTokens(kernel, totalBalance, ownerAddress)` — drains every KERNEL token from the contract.
3. Never call `unpause()` — users can never recover their vested allocation.

### Impact Explanation
**High — Theft of unclaimed yield.**

All KERNEL tokens allocated to the top-100 users under the vesting schedule can be permanently stolen. Users have a legitimate, merkle-proven entitlement to these tokens; the vesting schedule is already running. Pausing blocks the only two claim paths while `withdrawTokens()` lets the owner extract the full balance. The loss is total and irreversible.

### Likelihood Explanation
**Low.** The attack requires the contract owner to act maliciously or for the owner key to be compromised. No unprivileged actor can trigger this path. This matches the likelihood assessment of the reference report.

### Recommendation
Remove the `whenNotPaused` modifier from `claim()` and `claimAndStake()`, mirroring the reference report's recommendation. Users who have a merkle-proven vesting entitlement should always be able to withdraw tokens that have already vested, regardless of the contract's paused state. If a pause is needed for operational reasons (e.g., a discovered exploit in the staking path), it should not block the basic token-transfer claim path. Additionally, consider adding a `whenNotPaused` guard to `withdrawTokens()`, or restricting it so it cannot be called while any user's vesting period is still active.

### Proof of Concept
1. The vesting period starts (`vestingStartTimestamp` is set in `initialize`). Users' KERNEL allocations begin vesting linearly over 30 days.
2. Owner calls `pause()` at any point during the vesting window.
3. Any user who calls `claim()` or `claimAndStake()` receives `Pausable: paused` revert — no tokens are transferred.
4. Owner calls `withdrawTokens(address(kernel), kernel.balanceOf(address(this)), owner)` — succeeds because `withdrawTokens` has no `whenNotPaused` check.
5. The contract's KERNEL balance is now zero. Even if the owner later calls `unpause()`, all subsequent `claim()` calls will revert with an ERC-20 insufficient-balance error, permanently denying users their vested rewards. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-310)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L345-345)
```text
    function claimAndStake(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-471)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

        emit TokensWithdrawn(_token, _amount, _recipient);
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L474-477)
```text
    /// @notice Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }
```
