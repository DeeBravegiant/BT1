### Title
Owner Can Permanently Freeze Users' Unclaimed KERNEL Yield via Pause — (`contracts/KERNEL/KernelMerkleDistributor.sol`)

### Summary
The `KernelMerkleDistributor`, `KernelTop100MerkleDistributor`, and `MerkleDistributor` contracts all gate their `claim()` (and `claimAndStake()`) functions behind `whenNotPaused`, while `pause()` and `unpause()` are both restricted to `onlyOwner`. A malicious or compromised owner can permanently block all users from claiming their earned KERNEL token rewards by calling `pause()` and never calling `unpause()`.

### Finding Description
In `KernelMerkleDistributor.sol`, the user-facing `claim()` and `claimAndStake()` functions carry the `whenNotPaused` modifier: [1](#0-0) [2](#0-1) 

Both `pause()` and `unpause()` are gated by `onlyOwner`, meaning only the owner can lift the pause: [3](#0-2) 

The identical pattern exists in `MerkleDistributor.sol`: [4](#0-3) [5](#0-4) 

And in `KernelTop100MerkleDistributor.sol`: [6](#0-5) [7](#0-6) 

`KernelTop100MerkleDistributor` additionally exposes a `withdrawTokens()` function callable by the owner at any time, including while paused: [8](#0-7) 

This means in `KernelTop100MerkleDistributor`, a malicious owner can pause the contract, drain all KERNEL tokens via `withdrawTokens()`, and users permanently lose their unclaimed yield — escalating the impact beyond a simple freeze.

### Impact Explanation
Users who have earned KERNEL token rewards (verified by a merkle proof) are unable to call `claim()` while the contract is paused. Since only the owner can unpause, a malicious owner can indefinitely block all reward collection. For `KernelTop100MerkleDistributor`, the owner can additionally drain the contract's token balance while it is paused, causing permanent, irrecoverable loss of unclaimed yield.

**Impact classification:** Medium — Permanent freezing of unclaimed yield (all three contracts); escalates toward Critical theft of unclaimed yield for `KernelTop100MerkleDistributor` due to `withdrawTokens()`.

### Likelihood Explanation
The owner is a single EOA or multisig with no on-chain time-lock enforced at the contract level. Any key compromise, insider threat, or governance failure results in this attack path being exercisable in a single transaction. The pause mechanism is already deployed and callable with no preconditions.

### Recommendation
Remove `whenNotPaused` from `claim()` and `claimAndStake()` so users can always collect yield they have already earned, regardless of the contract's operational state. Pausing should only block new deposits or administrative operations, not the withdrawal of already-accrued rewards. Alternatively, introduce a time-locked unpause or a guardian role that can unpause independently of the owner.

### Proof of Concept
1. Users accumulate KERNEL rewards; the owner calls `setMerkleRoot()` to publish the distribution.
2. Owner calls `pause()` on `KernelMerkleDistributor`.
3. Any user calling `claim(index, account, cumulativeAmount, merkleProof)` receives a revert from the `whenNotPaused` modifier.
4. Owner never calls `unpause()`. All users' KERNEL rewards are permanently frozen in the contract.
5. For `KernelTop100MerkleDistributor`: owner additionally calls `withdrawTokens(kernelAddress, balance, ownerAddress)` while paused, draining all KERNEL tokens and making the freeze permanent and lossy.

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L250-265)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        nonReentrant
        whenNotPaused
    {
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);

        emit Claimed(index, account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L270-285)
```text
    function claimAndStake(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        nonReentrant
        whenNotPaused
    {
        uint256 amountToStake = _processClaim(index, account, cumulativeAmount, merkleProof);

        IKernelDepositPool(kernelDepositPool).stakeFor(account, amountToStake);

        emit ClaimedAndStaked(index, account, amountToStake);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L415-423)
```text
    /// @dev Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }

    /// @dev Unpauses the contract
    function unpause() external onlyOwner {
        _unpause();
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-106)
```text
    function claim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        external
        override
        whenNotPaused
    {
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L209-216)
```text
    function pause() external onlyOwner {
        _pause();
    }

    /// @dev Unpause the contract
    function unpause() external onlyOwner {
        _unpause();
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L310-311)
```text
    function claim(uint256 amount, bytes32[] calldata merkleProof) external nonReentrant whenNotPaused {
        address user = msg.sender;
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L474-482)
```text
    /// @notice Pauses the contract
    function pause() external onlyOwner {
        _pause();
    }

    /// @notice Unpauses the contract
    function unpause() external onlyOwner {
        _unpause();
    }
```
