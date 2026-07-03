### Title
`removeAllowedToken()` Permanently Freezes Token Balances in `RsETHTokenWrapper` Without Balance Check - (File: contracts/L2/RsETHTokenWrapper.sol)

---

### Summary

`RsETHTokenWrapper.removeAllowedToken()` sets `allowedTokens[_asset] = false` with no check on whether the contract still holds a balance of that token. After removal, `_withdraw()` unconditionally reverts for that token, freezing all deposited balances with no recovery path.

---

### Finding Description

`RsETHTokenWrapper` is an L2 wrapper that accepts multiple alternative rsETH tokens and mints `wrsETH` 1:1. Two deposit paths exist:

1. **`_deposit()`** — any user deposits an allowed token and receives minted `wrsETH`.
2. **`depositBridgerAssets()`** — a privileged bridger deposits tokens to collateralize already-minted `wrsETH` without minting new shares.

The removal function is:

```solidity
// contracts/L2/RsETHTokenWrapper.sol:180-185
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
``` [1](#0-0) 

There is no check on `ERC20Upgradeable(_asset).balanceOf(address(this))` before flipping the flag. The sole withdrawal path is:

```solidity
// contracts/L2/RsETHTokenWrapper.sol:120-128
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
``` [2](#0-1) 

Once `allowedTokens[_asset]` is `false`, every call to `withdraw(asset, ...)` or `withdrawTo(asset, ...)` reverts. There is no emergency-withdrawal function, no admin sweep, and no alternative exit for that specific token. The contract holds the balance indefinitely. [3](#0-2) 

---

### Impact Explanation

Two classes of funds are frozen simultaneously:

- **User-deposited tokens**: Users who called `deposit(asset, amount)` received `wrsETH` and now cannot redeem it for the removed token. Their `wrsETH` can only be redeemed for other still-allowed tokens, but the removed token's balance sits unreachable in the contract.
- **Bridger-deposited collateral**: Tokens deposited via `depositBridgerAssets()` were never paired with minted `wrsETH`, so no burn-path exists at all. Those tokens are permanently unrecoverable unless the token is re-added. [4](#0-3) 

The freeze persists until `TIMELOCK_ROLE` re-adds the token via `addAllowedToken()`, which itself is subject to a timelock delay. During that window the freeze is effective. If the token is never re-added, the freeze is permanent.

**Impact**: Temporary (potentially permanent) freezing of user funds — Medium per the allowed impact scope.

---

### Likelihood Explanation

The `TIMELOCK_ROLE` holder is expected to call `removeAllowedToken()` during routine token deprecation (e.g., migrating from one bridge variant to another). The function provides no warning and no balance guard, making it easy to trigger inadvertently while the contract still holds a non-zero balance. No attacker action is required; a single governance transaction suffices. [5](#0-4) 

---

### Recommendation

Add a balance guard before clearing the flag, mirroring the pattern used in `RSETHPoolV3.removeSupportedToken()`:

```solidity
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    if (ERC20Upgradeable(_asset).balanceOf(address(this)) != 0)
        revert TokenBalanceNotZero();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
```

Alternatively, add an admin sweep function that transfers a specific token to a recovery address regardless of its `allowedTokens` status, so that funds can always be recovered even after removal. [6](#0-5) 

---

### Proof of Concept

1. `TIMELOCK_ROLE` calls `addAllowedToken(tokenA)` — `allowedTokens[tokenA] = true`.
2. Alice calls `deposit(tokenA, 100e18)` — 100 `tokenA` enter the contract, 100 `wrsETH` minted to Alice.
3. Bridger calls `depositBridgerAssets(tokenA, 50e18)` — 50 more `tokenA` enter the contract, no `wrsETH` minted.
4. Contract now holds 150 `tokenA`.
5. `TIMELOCK_ROLE` calls `removeAllowedToken(tokenA)` — succeeds with no revert (no balance check).
6. Alice calls `withdraw(tokenA, 100e18)` → reverts: `TokenNotAllowed()`.
7. 150 `tokenA` are frozen in the contract. Alice's 100 `wrsETH` cannot be redeemed for `tokenA`. The bridger's 50 `tokenA` have no redemption path at all. [7](#0-6)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L84-94)
```text
    function withdraw(address asset, uint256 _amount) external {
        _withdraw(asset, msg.sender, _amount);
    }

    /// @dev Withdraw altRsETH tokens from wrsETH to a user
    /// @param asset The address of the token to withdraw
    /// @param _to The user to withdraw to
    /// @param _amount The amount of tokens to withdraw
    function withdrawTo(address asset, address _to, uint256 _amount) external {
        _withdraw(asset, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L162-170)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-185)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }

    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L559-568)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
    }
```
