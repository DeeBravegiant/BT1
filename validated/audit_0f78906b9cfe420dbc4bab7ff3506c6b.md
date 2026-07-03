### Title
Missing `msg.sender` Authorization in `MerkleDistributor.claim` Allows Anyone to Force Fee-Extracting Claims on Behalf of Any User - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary

`MerkleDistributor.claim` accepts an arbitrary `account` parameter with no check that `account == msg.sender`. Any external caller can supply a victim's address, a valid public Merkle proof, and trigger a claim on the victim's behalf. Because the contract deducts a protocol fee (up to 10%) before transferring tokens to `account`, the victim permanently loses the fee portion of their unclaimed yield without ever consenting to the claim.

### Finding Description

The `MerkleDistributor.claim` function at lines 97–147 accepts four caller-controlled parameters: `index`, `account`, `cumulativeAmount`, and `merkleProof`. The function verifies the Merkle proof against `(index, account, cumulativeAmount)` and then transfers `claimableAmount - fee` to `account` and `fee` to `protocolTreasury`. [1](#0-0) 

Critically, there is **no check** that `account == msg.sender`. The `account` parameter is entirely caller-controlled and is not part of any cryptographic commitment that binds it to the transaction sender. This is the direct analog to the external report's `ownerIndex` manipulation: a parameter outside the verified commitment can be freely set by any submitter to redirect execution against a victim. [2](#0-1) 

The sibling contract `KernelMerkleDistributor._processClaim` correctly guards against this with an explicit check: [3](#0-2) 

`MerkleDistributor` has no equivalent guard. The fee is configurable up to `MAX_FEE_IN_BPS = 1000` (10%): [4](#0-3) 

### Impact Explanation

An attacker forces a claim for any user whose Merkle proof is publicly available (all proofs are published off-chain for users to retrieve). The victim receives only `claimableAmount - fee` instead of `claimableAmount`. The fee — up to 10% of the victim's entire unclaimed allocation — is permanently redirected to `protocolTreasury`. The victim's `userClaims` state is updated to mark the full `cumulativeAmount` as claimed, so they can never recover the fee portion. This is **theft of unclaimed yield** (High severity).

The contract is deployed in production as the "KEP MerkleDistributor" (`0x2DDB11443bD9Ceb92d4951A05f55eb7096EB53d3`) and "EIGEN MerkleDistributor" contracts on Ethereum Mainnet, holding real user allocations. [5](#0-4) 

### Likelihood Explanation

- Merkle proofs are public data published off-chain for users to retrieve; any attacker can obtain valid proofs for any user.
- The attack requires no privileged access, no special role, and no capital at risk for the attacker.
- The attacker gains nothing directly (fee goes to treasury), but the victim permanently loses up to 10% of their allocation. A malicious actor could target all users in a single block to maximize damage.
- The entry path is a single public external call with no preconditions beyond knowing the victim's proof.

### Recommendation

Add a caller authorization check identical to the one in `KernelMerkleDistributor._processClaim`:

```solidity
if (account != msg.sender) {
    revert Unauthorized();
}
```

Insert this check at the top of `MerkleDistributor.claim`, before the Merkle proof verification. [6](#0-5) 

### Proof of Concept

```solidity
// Attacker observes victim's public Merkle proof data off-chain:
// index = 5, victimAddress = 0xVICTIM, cumulativeAmount = 1000e18, proof = [...]

// Attacker calls claim on behalf of victim (no special access needed):
merkleDistributor.claim(5, victimAddress, 1000e18, proof);

// Result:
// - victim receives 900e18 (if feeInBPS = 1000, i.e. 10%)
// - protocolTreasury receives 100e18
// - victim's userClaims.cumulativeAmount is set to 1000e18 (fully consumed)
// - victim can never recover the 100e18 fee
```

The attacker can repeat this for every user in the Merkle tree in a single block, permanently extracting up to 10% of the entire distribution from all users. [7](#0-6)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L47-51)
```text
    uint256 public constant MAX_FEE_IN_BPS = 1000;

    address public override token;
    address public protocolTreasury;
    uint256 public feeInBPS;
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L97-147)
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
