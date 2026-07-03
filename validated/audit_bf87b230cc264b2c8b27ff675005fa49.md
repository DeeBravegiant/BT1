### Title
Changing `token` in `MerkleDistributor` Will Permanently Freeze Existing Reward Balances - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor.sol` exposes a `setToken()` function that allows the owner to replace the `token` address at any time. Because `claim()` always reads the current `token` value at execution time, any previously deposited Token A that users have pending Merkle claims against becomes permanently unclaimable once `token` is changed to Token B. Token A is frozen in the contract with no recovery path.

### Finding Description
The `MerkleDistributor` contract stores a single mutable `token` address used for all claim payouts. The `initialize()` function explicitly notes that the token can be set later:

```solidity
// token can be set later but not the protocol treasury
token = token_;
``` [1](#0-0) 

The owner can call `setToken()` at any time with no guard on existing balances or pending claims:

```solidity
function setToken(address _token) external onlyOwner {
    if (_token == address(0)) {
        revert ZeroValueProvided();
    }
    token = _token;
    emit TokenUpdated(_token);
}
``` [2](#0-1) 

The `claim()` function unconditionally reads the current `token` state variable when transferring:

```solidity
IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);
``` [3](#0-2) 

There is no mechanism that ties a Merkle root to the token address that was active when the root was set. The Merkle leaf only encodes `(index, account, cumulativeAmount)` — not the token:

```solidity
bytes32 node = keccak256(abi.encodePacked(index, account, cumulativeAmount));
``` [4](#0-3) 

### Impact Explanation
When `setToken(tokenB)` is called while Token A is held in the contract and users have unclaimed Merkle allocations:

1. All `claim()` calls attempt `IERC20(tokenB).safeTransfer(...)`.
2. The contract holds zero Token B, so every transfer reverts.
3. Token A is permanently locked in the contract with no admin withdrawal function.
4. Users can never claim their entitled Token A rewards.

This constitutes **permanent freezing of unclaimed yield** (Medium severity per the allowed impact scope).

### Likelihood Explanation
The `initialize()` comment explicitly states the token is designed to be set post-deployment. This makes `setToken()` a routine operational call, not an edge case. Any operator following the intended deployment flow — deploy with `token_ = address(0)`, fund the contract with Token A, set the Merkle root, then call `setToken(tokenA)` — and who later migrates to a new token contract will trigger this freeze for all users who have not yet claimed.

### Recommendation
Either:
1. Make `token` immutable and remove `setToken()`, or
2. Add a guard in `setToken()` that requires the contract's current token balance to be zero before allowing the change:
   ```solidity
   if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
   ```

### Proof of Concept
1. Owner deploys `MerkleDistributor` with `token_ = address(tokenA)`.
2. Owner deposits 1000 tokenA into the contract.
3. Owner calls `setMerkleRoot(root)` where `root` encodes user Alice → 500 tokenA.
4. Alice has not yet called `claim()`.
5. Owner calls `setToken(address(tokenB))`.
6. Alice calls `claim(index, alice, 500, proof)`.
7. The contract executes `IERC20(tokenB).safeTransfer(alice, 500)` — reverts because the contract holds 0 tokenB.
8. Alice's 500 tokenA is permanently frozen in the contract. `isClaimed` was never updated, so Alice is not marked as claimed, but every future attempt also reverts for the same reason. [5](#0-4)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L71-87)
```text
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L185-193)
```text
    function setToken(address _token) external onlyOwner {
        if (_token == address(0)) {
            revert ZeroValueProvided();
        }

        token = _token;

        emit TokenUpdated(_token);
    }
```
