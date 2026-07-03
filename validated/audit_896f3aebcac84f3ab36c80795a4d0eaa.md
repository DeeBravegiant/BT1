Audit Report

## Title
Zero-Amount Fee Transfer in `claim()` Freezes User Rewards When `feeInBPS == 0` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

## Summary
`MerkleDistributor.claim()` unconditionally executes `IERC20(token).safeTransfer(protocolTreasury, fee)` at line 144 even when `fee` evaluates to zero. Since `feeInBPS = 0` is a valid and reachable state, and the contract is token-agnostic, deploying it with a token that reverts on zero-value transfers causes every `claim()` call to revert, permanently freezing all unclaimed allocations.

## Finding Description
In `MerkleDistributor.sol`, the `claim()` function computes the fee and then unconditionally transfers it:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;   // = 0 when feeInBPS == 0
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);
IERC20(token).safeTransfer(protocolTreasury, fee);      // always called, reverts when fee == 0
``` [1](#0-0) 

Both `initialize()` and `setFeeInBPS()` permit `_feeInBPS = 0` — the only guard is `> MAX_FEE_IN_BPS`: [2](#0-1) [3](#0-2) 

The token is also freely configurable by the owner via `setToken()`, making the contract token-agnostic: [4](#0-3) 

The sibling contract `KernelTop100MerkleDistributor` correctly guards the fee transfer, confirming the intended pattern: [5](#0-4) 

`MerkleDistributor` lacks this guard entirely. Since `claim()` is the sole withdrawal path (no rescue or alternative claim function exists), all unclaimed allocations are permanently locked.

## Impact Explanation
When `feeInBPS == 0` and the configured token reverts on zero-value transfers, every call to `claim()` reverts after the user's state has not yet been updated (state update occurs before the transfer, but the revert rolls it back). No user can ever receive their allocated tokens. This constitutes **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation
- `feeInBPS = 0` is the default if initialized with `_feeInBPS = 0`, and the owner can set it to zero at any time via `setFeeInBPS(0)` — no malicious intent required; zero fee is a legitimate operational choice.
- The token is configurable via `setToken()`, and the class of ERC-20 tokens that revert on zero-value transfers is well-documented and non-trivial in size.
- No attacker is needed; the condition arises from normal, permitted protocol configuration. Any user calling `claim()` under these conditions is affected.

## Recommendation
Guard the fee transfer with a zero-amount check, consistent with `KernelTop100MerkleDistributor`:

```solidity
uint256 fee = (claimableAmount * feeInBPS) / 10_000;
uint256 amountToSend = claimableAmount - fee;

IERC20(token).safeTransfer(account, amountToSend);

if (fee > 0) {
    IERC20(token).safeTransfer(protocolTreasury, fee);
}
```

## Proof of Concept
1. Deploy `MerkleDistributor` with a token that reverts on zero-value transfers and `feeInBPS = 0` (or call `setFeeInBPS(0)` post-deployment).
2. Set a valid Merkle root containing an allocation for `alice`.
3. `alice` calls `claim(index, alice, cumulativeAmount, proof)`.
4. `fee = (claimableAmount * 0) / 10_000 = 0`.
5. `IERC20(token).safeTransfer(alice, claimableAmount)` succeeds.
6. `IERC20(token).safeTransfer(protocolTreasury, 0)` **reverts** — entire transaction rolls back.
7. `alice`'s allocation remains locked indefinitely; no alternative claim path exists.

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L77-79)
```text
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L138-144)
```text
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
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

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L198-201)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }
```

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L332-334)
```text
        if (fee > 0) {
            kernel.safeTransfer(protocolTreasury, fee);
        }
```
