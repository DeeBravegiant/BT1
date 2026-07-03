### Title
Merkle Tree Total Not Verified Against Deposited Token Balance Allows Over-Claiming That Drains Future Claimants' Rewards - (File: contracts/KERNEL/KernelMerkleDistributor.sol)

### Summary
`KernelMerkleDistributor` holds a single shared pool of KERNEL tokens across all distribution periods. When a new Merkle root is set via `setMerkleRoot()`, no on-chain record is kept of how many tokens were deposited for that specific period, and no check verifies that the sum of all `cumulativeAmount` values in the new tree is consistent with the contract's actual balance. If the off-chain Merkle tree generation script produces a tree whose total claimable sum exceeds the tokens actually deposited, early claimants drain the shared pool and later claimants permanently lose their unclaimed KERNEL rewards.

### Finding Description
`setMerkleRoot()` accepts a new Merkle root and increments `currentMerkleRootIndex` and `currentIndex`, but records no `totalAllocated` amount for the period and performs no balance check:

```solidity
function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
    if (_merkleRootToSet == bytes32(0)) { revert ZeroValueProvided(); }
    currentMerkleRoot = _merkleRootToSet;
    currentMerkleRootIndex++;
    currentIndex++;
    emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
}
``` [1](#0-0) 

All KERNEL tokens for every period sit in one undifferentiated balance. Claims are processed by subtracting the user's previously recorded `cumulativeAmount` from the new `cumulativeAmount` in the tree:

```solidity
uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;
``` [2](#0-1) 

There is no per-period token bucket and no invariant enforced on-chain that `sum(cumulativeAmount_new - cumulativeAmount_old for all users) ≤ tokens deposited for this period`. The contract has no `withdrawTokens` recovery function either, so any over-drained balance is unrecoverable. [3](#0-2) 

### Impact Explanation
If the off-chain Merkle tree generation script has a bug and produces a tree where the sum of all claimable increments exceeds the tokens deposited for that period, users who claim early drain the shared pool. Users who claim later receive a revert from `safeTransfer` due to insufficient balance, permanently losing their unclaimed KERNEL yield. This is a **High** impact: permanent freezing / theft of unclaimed yield for affected claimants. [4](#0-3) 

### Likelihood Explanation
**Low.** The trigger requires a bug in the off-chain Merkle tree generation script that causes the sum of claimable amounts to exceed the deposited balance. This is the same likelihood assessment as the referenced report: the event is unlikely but the on-chain contract provides no safeguard to limit the blast radius when it does occur. [1](#0-0) 

### Recommendation
1. When calling `setMerkleRoot()`, also pass a `uint256 totalNewAllocation` parameter representing the net new tokens being added for this period. Record it in a `mapping(uint256 periodIndex => uint256 allocated)` storage variable.
2. Require that `kernel.balanceOf(address(this))` increases by at least `totalNewAllocation` before or atomically with the root update (or verify the balance is sufficient).
3. During `_processClaim`, deduct from the period's allocation bucket so that the invariant `claimed ≤ allocated` is enforced per period, preventing one period's over-issuance from draining another period's tokens. [5](#0-4) 

### Proof of Concept
1. Owner deposits 1,000 KERNEL for period 1 and calls `setMerkleRoot(root1)`. The tree correctly encodes 1,000 KERNEL total across all users.
2. Owner deposits 500 KERNEL for period 2 (contract now holds 1,500 KERNEL) and calls `setMerkleRoot(root2)`. Due to an off-chain script bug, `root2` encodes 2,000 KERNEL total (500 inflated).
3. Users A–Z claim against `root2`. The first claimants succeed; `kernel.safeTransfer` drains the 1,500 KERNEL balance.
4. Remaining users call `claim()`. `_processClaim` computes a non-zero `claimableAmount`, but `kernel.safeTransfer(account, amountToSend)` reverts with `ERC20InsufficientBalance` because the pool is empty.
5. Those users permanently lose their unclaimed KERNEL rewards with no recovery path, since `KernelMerkleDistributor` has no `withdrawTokens` function. [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L133-180)
```text
contract KernelMerkleDistributor is
    IMerkleDistributor,
    Initializable,
    OwnableUpgradeable,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
    using SafeERC20 for IERC20;

    /// @notice The fee denominator constant used to calculate the fee
    uint256 public constant FEE_DENOMINATOR = 10_000;

    /// @notice The maximum fee in basis points that can be set by the owner (10%)
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    /// @notice The KERNEL token distributed by this contract
    IERC20 public kernel;

    /// @notice The address of the protocol treasury
    address public protocolTreasury;

    /// @notice The fee in basis points
    uint256 public feeInBPS;

    /// @notice The KernelDepositPool contract address
    IKernelDepositPool public kernelDepositPool;

    /// @notice The current index of the current merkle root
    uint256 public currentMerkleRootIndex;

    /// @notice The current merkle root
    bytes32 public currentMerkleRoot;

    /// @notice The current index
    uint256 public currentIndex;

    /**
     * @notice The UserClaim struct
     * @param lastClaimedIndex The last claimed index
     * @param cumulativeAmount The cumulative amount claimed
     */
    struct UserClaim {
        uint256 lastClaimedIndex;
        uint256 cumulativeAmount;
    }

    /// @notice The user claims mapping
    mapping(address user => UserClaim userClaim) public userClaims;
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L261-265)
```text
        uint256 amountToSend = _processClaim(index, account, cumulativeAmount, merkleProof);

        kernel.safeTransfer(account, amountToSend);

        emit Claimed(index, account, amountToSend);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L292-346)
```text
    function _processClaim(
        uint256 index,
        address account,
        uint256 cumulativeAmount,
        bytes32[] calldata merkleProof
    )
        internal
        returns (uint256)
    {
        UtilLib.checkNonZeroAddress(account);

        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (account != msg.sender) {
            revert Unauthorized();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof
        bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
        if (!MerkleProofUpgradeable.verify(merkleProof, currentMerkleRoot, node)) {
            revert InvalidMerkleProof();
        }

        // Calculate the claimable amount
        uint256 claimableAmount = cumulativeAmount - userClaims[account].cumulativeAmount;

        // Ensure there is something to claim
        if (claimableAmount == 0) {
            revert NoTokensToClaim();
        }

        // Update user claim info
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Calculate the fee and the amount to send
        uint256 fee = (claimableAmount * feeInBPS) / FEE_DENOMINATOR;
        uint256 amountToSend = claimableAmount - fee;

        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }

        return amountToSend;
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L402-413)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```
