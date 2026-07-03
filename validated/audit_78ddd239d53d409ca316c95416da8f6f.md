### Title
Removed Allowed Tokens Permanently Freeze User Funds in Wrapper Contracts — (`contracts/L2/RsETHTokenWrapper.sol`, `contracts/agETH/AGETHTokenWrapper.sol`)

---

### Summary

Both `RsETHTokenWrapper` and `AGETHTokenWrapper` apply the same `allowedTokens` check to both deposit **and** withdrawal operations. When an admin removes a token from the allowed list, users who have already deposited that token and hold wrapper tokens (`wrsETH` / wrapped `agETH`) can no longer withdraw their underlying assets, permanently freezing their funds in the contract.

---

### Finding Description

In `RsETHTokenWrapper._withdraw`, the check at line 121 gates withdrawals on the current state of `allowedTokens`: [1](#0-0) 

The same pattern exists in `AGETHTokenWrapper._withdraw`: [2](#0-1) 

The admin (via `TIMELOCK_ROLE` in `RsETHTokenWrapper`, or `DEFAULT_ADMIN_ROLE` in `AGETHTokenWrapper`) can remove a token from the allowed list: [3](#0-2) [4](#0-3) 

Once `allowedTokens[_asset]` is set to `false`, every call to `withdraw(asset, amount)` or `withdrawTo(asset, to, amount)` for that asset reverts with `TokenNotAllowed()`. The underlying `altRsETH` (or `altAgETH`) tokens remain locked in the contract with no recovery path for users. Their `wrsETH` / wrapper tokens become unredeemable.

The deposit path has the same check, which is correct — new deposits of a de-listed token should be blocked. The flaw is that the **withdrawal path shares the same gate**, meaning de-listing a token simultaneously blocks both new deposits and existing withdrawals.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

Any user who deposited `altRsETH` into `RsETHTokenWrapper` (or `altAgETH` into `AGETHTokenWrapper`) before the token was removed from the allowed list loses access to their underlying assets permanently. Their wrapper tokens (`wrsETH` / `agETH`) cannot be redeemed for anything. The locked assets remain in the contract indefinitely with no on-chain escape path.

---

### Likelihood Explanation

**Medium.** Token removal is a legitimate, expected admin operation — for example, when migrating to a new bridge token version, deprecating a chain-specific variant, or responding to a discovered bug in the alt token. The admin may not anticipate that this action simultaneously freezes all existing user positions. The `AGETHTokenWrapper` variant is even more accessible since `removeAllowedToken` requires only `DEFAULT_ADMIN_ROLE` rather than a timelock. [4](#0-3) 

---

### Recommendation

Remove the `allowedTokens` check from `_withdraw`. Only gate **deposits** on the allowed list. Withdrawals should always be permitted for any token the contract holds, regardless of its current allowed status. Optionally, maintain a separate `nonWithdrawableTokens` set for tokens that must never be withdrawn (analogous to the "stop policy" in the original M-16 recommendation).

```solidity
// BEFORE (blocks withdrawals of de-listed tokens):
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ...
}

// AFTER (only deposits are gated):
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    // No allowedTokens check — users can always redeem what they deposited
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

---

### Proof of Concept

1. User calls `deposit(altRsETH, 100e18)` on `RsETHTokenWrapper` while `altRsETH` is allowed. [5](#0-4) 
   User receives 100e18 `wrsETH`.

2. Admin calls `removeAllowedToken(altRsETH)` — a legitimate operation (e.g., token migration). [3](#0-2) 
   `allowedTokens[altRsETH]` is now `false`.

3. User calls `withdraw(altRsETH, 100e18)` to recover their underlying tokens. [1](#0-0) 
   Transaction reverts with `TokenNotAllowed()`.

4. The user's 100e18 `altRsETH` is permanently locked in the contract. Their `wrsETH` balance is unredeemable. There is no admin rescue function, no alternative withdrawal path, and no on-chain mechanism to recover the funds.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-122)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

```

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L180-185)
```text
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-113)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L157-160)
```text
    function removeAllowedToken(address _asset) external onlyRole(DEFAULT_ADMIN_ROLE) {
        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```
