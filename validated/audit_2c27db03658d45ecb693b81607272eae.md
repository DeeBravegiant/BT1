### Title
`TokenSwap.removeSupportedToken` Does Not Check for Existing Token Balance, Causing Temporary Fund Freeze - (File: contracts/king-protocol/TokenSwap.sol)

---

### Summary

`TokenSwap.removeSupportedToken` removes a token from the supported list without verifying whether the contract still holds a balance of that token. Any balance remaining in the contract after removal becomes inaccessible through normal protocol functions, requiring out-of-band admin intervention to recover.

---

### Finding Description

In `contracts/king-protocol/TokenSwap.sol`, the `removeSupportedToken` function (callable by `MANAGER_ROLE`) delegates to `_removeSupportedToken`, which sets `supportedTokens[token] = false` and removes the token from `supportedTokensList` with no balance check: [1](#0-0) [2](#0-1) 

After removal, `depositToKingProtocol` enforces:

```solidity
if (!supportedTokens[asset]) {
    revert UnsupportedAsset();
}
``` [3](#0-2) 

This means any token balance sitting in the `TokenSwap` contract at the time of removal can no longer be forwarded to King Protocol through the normal path. The only recovery route is the admin-only `emergencyWithdraw`: [4](#0-3) 

This is a role separation issue: `MANAGER_ROLE` can trigger the freeze; only `DEFAULT_ADMIN_ROLE` can undo it. The two roles are distinct.

By contrast, other `removeSupportedToken` implementations in the codebase (e.g., `RSETHPoolV3`, `RSETHPool`, `AGETHPoolV3`) all guard against this by reverting if the contract still holds a balance:

```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
``` [5](#0-4) [6](#0-5) 

`TokenSwap` is the only contract in the codebase that omits this guard.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Any token balance held by `TokenSwap` at the time of removal becomes inaccessible via `depositToKingProtocol`. The funds are not permanently lost (admin can call `emergencyWithdraw`), but they are frozen until admin intervention. In a normal operational flow, tokens are transferred into `TokenSwap` and then batched into King Protocol; a removal during this window freezes the in-transit balance.

---

### Likelihood Explanation

`MANAGER_ROLE` is a privileged but operationally active role. The `TokenSwap` contract is designed to hold token balances between receipt and deposit to King Protocol. A manager removing a token that still has a pending balance — whether by mistake or during a token migration — is a realistic operational scenario. No attacker-controlled input is required; the manager's own routine action triggers the freeze.

---

### Recommendation

Add a balance check in `removeSupportedToken` (or `_removeSupportedToken`) before removing the token, consistent with every other pool contract in the codebase:

```solidity
function removeSupportedToken(address token) external onlyManager {
    if (!supportedTokens[token]) revert TokenNotSupported();
    if (supportedTokensList.length <= 1) revert CannotRemoveLastToken();
+   if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
    _removeSupportedToken(token);
}
```

---

### Proof of Concept

1. Manager calls `addSupportedToken(tokenA)` — `tokenA` is now supported.
2. External party (or protocol flow) transfers 1000 `tokenA` into `TokenSwap`.
3. Manager calls `removeSupportedToken(tokenA)` — succeeds with no revert; `supportedTokens[tokenA]` is now `false`.
4. Manager attempts `depositToKingProtocol(tokenA, 1000)` — reverts with `UnsupportedAsset`.
5. The 1000 `tokenA` are frozen in the contract.
6. Only admin can recover them via `emergencyWithdraw(tokenA, recipient, 1000)`. [1](#0-0) [7](#0-6)

### Citations

**File:** contracts/king-protocol/TokenSwap.sol (L145-161)
```text
    function depositToKingProtocol(
        address asset,
        uint256 amount
    )
        external
        nonReentrant
        whenNotPaused
        onlyAdminOrManager
        returns (uint256 shareReceived)
    {
        if (amount == 0) {
            revert ZeroAmount();
        }

        if (!supportedTokens[asset]) {
            revert UnsupportedAsset();
        }
```

**File:** contracts/king-protocol/TokenSwap.sol (L439-449)
```text
    function removeSupportedToken(address token) external onlyManager {
        if (!supportedTokens[token]) {
            revert TokenNotSupported();
        }

        if (supportedTokensList.length <= 1) {
            revert CannotRemoveLastToken();
        }

        _removeSupportedToken(token);
    }
```

**File:** contracts/king-protocol/TokenSwap.sol (L468-481)
```text
    function _removeSupportedToken(address token) internal {
        supportedTokens[token] = false;

        // Find and remove from array
        for (uint256 i = 0; i < supportedTokensList.length; i++) {
            if (supportedTokensList[i] == token) {
                supportedTokensList[i] = supportedTokensList[supportedTokensList.length - 1];
                supportedTokensList.pop();
                break;
            }
        }

        emit SupportedTokenRemoved(token);
    }
```

**File:** contracts/king-protocol/TokenSwap.sol (L487-496)
```text
    function emergencyWithdraw(address token, address recipient, uint256 amount) external onlyAdmin {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(recipient);

        if (amount == 0) {
            revert ZeroAmount();
        }

        IERC20(token).safeTransfer(recipient, amount);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L559-562)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

**File:** contracts/agETH/AGETHPoolV3.sol (L290-293)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```
