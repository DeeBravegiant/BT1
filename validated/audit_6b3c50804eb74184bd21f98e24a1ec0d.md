### Title
Missing `msg.sender == account` Check in `MerkleDistributor.claim()` Allows Any Caller to Force-Claim on Behalf of Any User, Extracting Protocol Fee Without Consent - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

---

### Summary

`MerkleDistributor.claim()` accepts an arbitrary `account` address but never verifies that `msg.sender == account`. Any external caller can supply a valid merkle proof (which is public off-chain data) and force-trigger a claim for any user. This permanently consumes the victim's claim state and extracts the protocol fee from their allocation without their knowledge or consent.

---

### Finding Description

`MerkleDistributor.claim()` takes `account` as a caller-supplied parameter and uses it to verify the merkle proof, update `userClaims[account]`, and transfer tokens — but there is no check that `msg.sender` is the owner of `account`:

```solidity
// contracts/utils/MerkleDistributor/MerkleDistributor.sol
function claim(
    uint256 index,
    address account,       // <-- caller-supplied, never verified against msg.sender
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
    // ...no msg.sender == account check...

    userClaims[account].lastClaimedIndex = index;
    userClaims[account].cumulativeAmount = cumulativeAmount;

    uint256 fee = (claimableAmount * feeInBPS) / 10_000;
    uint256 amountToSend = claimableAmount - fee;

    IERC20(token).safeTransfer(account, amountToSend);
    IERC20(token).safeTransfer(protocolTreasury, fee);   // fee extracted from victim
}
``` [1](#0-0) 

The sibling contract `KernelMerkleDistributor` correctly guards against this exact pattern inside `_processClaim()`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
``` [2](#0-1) 

`MerkleDistributor.sol` contains zero references to `msg.sender` anywhere in the file, confirming the check is entirely absent. [3](#0-2) 

---

### Impact Explanation

**Severity: High — Theft of unclaimed yield.**

When an attacker force-claims for a victim:

1. `userClaims[account]` is permanently updated — the victim's claim slot is consumed and they cannot claim again at a time of their choosing.
2. The protocol fee (`feeInBPS`, up to `MAX_FEE_IN_BPS = 1000`, i.e. 10%) is deducted from the victim's allocation and sent to `protocolTreasury`. The victim had no opportunity to wait for a fee reduction (the owner can lower `feeInBPS` via `setFeeInBPS`).
3. The victim loses up to 10% of their entitled token allocation without ever interacting with the contract. [4](#0-3) [5](#0-4) 

The attacker gains nothing directly, but the victim suffers a permanent, irreversible loss of yield from their merkle allocation.

---

### Likelihood Explanation

**High.** The attack requires:
- Knowledge of the victim's `index`, `account`, `cumulativeAmount`, and `merkleProof` — all of which are public off-chain data (merkle trees are published for users to self-claim).
- No special role, no capital, no front-running dependency.

Any unprivileged external caller can execute this against any unclaimed account at any time the contract is unpaused.

---

### Recommendation

Add a caller ownership check at the top of `claim()`, mirroring the pattern already used in `KernelMerkleDistributor._processClaim()`:

```solidity
function claim(
    uint256 index,
    address account,
    uint256 cumulativeAmount,
    bytes32[] calldata merkleProof
) external override whenNotPaused {
+   if (account != msg.sender) revert Unauthorized();
    // ...rest of logic
}
``` [2](#0-1) 

---

### Proof of Concept

1. The protocol publishes a merkle tree. Alice's leaf encodes `(index=5, account=Alice, cumulativeAmount=1000e18)`. The merkle proof is public.
2. The current `feeInBPS = 500` (5%). Alice is waiting for the owner to call `setFeeInBPS(0)` before claiming.
3. Attacker calls `MerkleDistributor.claim(5, Alice, 1000e18, aliceProof)` before the fee is reduced.
4. The contract verifies the proof (valid), updates `userClaims[Alice]`, transfers `950e18` tokens to Alice, and transfers `50e18` tokens to `protocolTreasury`.
5. Alice's claim is now permanently consumed. She cannot claim again. She has lost `50e18` tokens she would have received had she been allowed to claim after the fee was zeroed. [6](#0-5)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L44-147)
```text
contract MerkleDistributor is IMerkleDistributor, OwnableUpgradeable, PausableUpgradeable {
    using SafeERC20 for IERC20;

    uint256 public constant MAX_FEE_IN_BPS = 1000;

    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;

    uint256 public currentMerkleRootIndex;
    bytes32 public currentMerkleRoot;

    uint256 public currentIndex;

    struct UserClaim {
        uint256 lastClaimedIndex;
        uint256 cumulativeAmount;
    }

    mapping(address user => UserClaim userClaim) public userClaims;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Initializes the contract
    function initialize(address token_, address _protocolTreasury, uint256 _feeInBPS) external initializer {
        // token can be set later but not the protocol treasury
        if (_protocolTreasury == address(0)) {
            revert ZeroValueProvided();
        }

        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        __Ownable_init();
        __Pausable_init();

        token = token_;
        protocolTreasury = _protocolTreasury;
        feeInBPS = _feeInBPS;
    }

    /// @inheritdoc IMerkleDistributor
    function isClaimed(uint256 index, address account) public view override returns (bool) {
        if (index == 0) revert ZeroValueProvided();

        return userClaims[account].lastClaimedIndex >= index;
    }

    /// @inheritdoc IMerkleDistributor
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
        if (currentMerkleRoot == bytes32(0)) {
            revert ZeroValueProvided();
        }

        if (index == 0 || index > currentIndex) {
            revert InvalidIndex();
        }

        if (isClaimed(index, account)) {
            revert AlreadyClaimed();
        }

        // Verify the merkle proof.
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

        // Update user claim info, and send the token.
        userClaims[account].lastClaimedIndex = index;
        userClaims[account].cumulativeAmount = cumulativeAmount;

        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);

        emit Claimed(index, account, claimableAmount);
    }
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L311-313)
```text
        if (account != msg.sender) {
            revert Unauthorized();
        }
```
