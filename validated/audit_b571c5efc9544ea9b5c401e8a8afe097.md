### Title
Single-Step Ownership Transfer Allows Permanent Loss of Admin Control and Fund Theft - (File: contracts/KERNEL/KernelTop100MerkleDistributor.sol)

### Summary
`KernelTop100MerkleDistributor`, `KernelMerkleDistributor`, `MerkleDistributor`, and `MerkleBlastPointsDistributor` all inherit from `OwnableUpgradeable`, which implements a single-step `transferOwnership`. If the owner accidentally transfers to a wrong address, ownership is immediately and irrevocably lost. The most severe case is `KernelTop100MerkleDistributor`, which exposes a `withdrawTokens` function callable only by the owner, enabling complete drainage of all KERNEL tokens held for user distribution.

### Finding Description
`KernelTop100MerkleDistributor` inherits `OwnableUpgradeable`: [1](#0-0) 

`OwnableUpgradeable.transferOwnership` immediately and atomically replaces the owner with no confirmation step: [2](#0-1) 

The owner of `KernelTop100MerkleDistributor` has access to `withdrawTokens`, which can drain any token balance from the contract: [3](#0-2) 

The same single-step pattern exists in `KernelMerkleDistributor`, `MerkleDistributor`, and `MerkleBlastPointsDistributor`, all of which inherit `OwnableUpgradeable`: [4](#0-3) [5](#0-4) [6](#0-5) 

By contrast, the protocol's own `ConfirmedOwnerWithProposal` (used by `WrappedRSETH`) correctly implements a two-step push/pull pattern: [7](#0-6) 

The distributor contracts do not use this safer pattern.

### Impact Explanation
**Critical.** `KernelTop100MerkleDistributor.withdrawTokens` allows the owner to transfer any amount of any token to any recipient. If ownership is accidentally transferred to a wrong address (e.g., a typo, a zero address, or an address front-run by an attacker), the new "owner" can immediately call `withdrawTokens` to drain all KERNEL tokens held in the contract — tokens that belong to users awaiting distribution. This constitutes direct theft of user funds at rest. For `KernelMerkleDistributor` and `MerkleDistributor`, the impact is at minimum permanent freezing of user funds (if the wrong address pauses and cannot unpause) and theft of unclaimed yield (if the wrong address redirects `protocolTreasury`). [3](#0-2) 

### Likelihood Explanation
**Low.** This requires the current owner to make an error when calling `transferOwnership` — a typo, copy-paste mistake, or a front-running attack on a pending ownership transfer transaction. While not routine, human error in address entry is a well-documented real-world failure mode, which is precisely why the two-step pattern exists. The protocol already acknowledges this risk by using `ConfirmedOwnerWithProposal` in `WrappedRSETH`, making the omission in the distributor contracts an inconsistency.

### Recommendation
Replace `OwnableUpgradeable` with `Ownable2StepUpgradeable` in all four distributor contracts. `Ownable2StepUpgradeable` requires the nominated new owner to call `acceptOwnership()`, ensuring the new address is live and controlled before the transfer completes. This is the exact push/pull pattern recommended in ADM-1 and already implemented in `ConfirmedOwnerWithProposal`.

### Proof of Concept
1. Admin holds ownership of `KernelTop100MerkleDistributor`. The contract holds 1,000,000 KERNEL tokens for user distribution.
2. Admin intends to transfer ownership to `newAdmin = 0xAAAA...` but mistakenly calls `transferOwnership(0xBBBB...)` (typo), or an attacker front-runs a pending `transferOwnership` transaction and substitutes their own address.
3. `OwnableUpgradeable.transferOwnership` immediately sets `_owner = 0xBBBB...` with no confirmation required.
4. The attacker (now owner at `0xBBBB...`) calls:
   ```solidity
   withdrawTokens(address(kernel), 1_000_000e18, attacker);
   ```
5. All 1,000,000 KERNEL tokens are transferred to the attacker. Users can no longer claim their allocated rewards. [8](#0-7) [3](#0-2)

### Citations

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L111-117)
```text
contract KernelTop100MerkleDistributor is
    IMerkleDistributor,
    Initializable,
    OwnableUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L398-410)
```text
    function setKernelDepositPool(address _kernelDepositPool) external onlyOwner {
        UtilLib.checkNonZeroAddress(_kernelDepositPool);

        address oldKernelDepositPool = address(kernelDepositPool);

        // Revoke old approval and set new one
        kernel.forceApprove(oldKernelDepositPool, 0);
        kernel.forceApprove(_kernelDepositPool, type(uint256).max);

        kernelDepositPool = IKernelDepositPool(_kernelDepositPool);

        emit KernelDepositPoolUpdated(_kernelDepositPool);
    }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-472)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

        emit TokensWithdrawn(_token, _amount, _recipient);
    }
```

**File:** lib/openzeppelin-contracts-upgradeable/contracts/access/OwnableUpgradeable.sol (L74-77)
```text
    function transferOwnership(address newOwner) public virtual onlyOwner {
        require(newOwner != address(0), "Ownable: new owner is the zero address");
        _transferOwnership(newOwner);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L133-139)
```text
contract KernelMerkleDistributor is
    IMerkleDistributor,
    Initializable,
    OwnableUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L44-44)
```text
contract MerkleDistributor is IMerkleDistributor, OwnableUpgradeable, PausableUpgradeable {
```

**File:** contracts/utils/MerkleDistributor/MerkleBlastPointsDistributor.sol (L48-48)
```text
contract MerkleBlastPointsDistributor is IMerkleBlastPointsDistributor, OwnableUpgradeable, PausableUpgradeable {
```

**File:** contracts/ccip/ConfirmedOwnerWithProposal.sol (L36-51)
```text
    function transferOwnership(address to) public override onlyOwner {
        _transferOwnership(to);
    }

    /**
     * @notice Allows an ownership transfer to be completed by the recipient.
     */
    function acceptOwnership() external override {
        require(msg.sender == s_pendingOwner, "Must be proposed owner");

        address oldOwner = s_owner;
        s_owner = msg.sender;
        s_pendingOwner = address(0);

        emit OwnershipTransferred(oldOwner, msg.sender);
    }
```
