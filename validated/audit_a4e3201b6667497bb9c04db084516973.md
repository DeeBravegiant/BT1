### Title
Tokens Permanently Frozen When Allowed Token Is Removed From `RsETHTokenWrapper` - (File: contracts/L2/RsETHTokenWrapper.sol)

### Summary
`RsETHTokenWrapper.sol` holds altRsETH tokens as 1:1 collateral for minted `wrsETH`. When `removeAllowedToken()` is called by the `TIMELOCK_ROLE`, the `_withdraw()` function immediately begins reverting with `TokenNotAllowed` for that asset, permanently freezing all altRsETH tokens held in the contract with no recovery path.

### Finding Description
`RsETHTokenWrapper` is a 1:1 lockbox: users deposit an allowed altRsETH token and receive `wrsETH`; they burn `wrsETH` to reclaim the altRsETH. The contract also accepts collateral deposits from the bridger via `depositBridgerAssets()` without minting new shares.

The `removeAllowedToken()` function simply flips the `allowedTokens` mapping to `false`:

```solidity
// contracts/L2/RsETHTokenWrapper.sol L180-L185
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
```

The internal `_withdraw()` function, which is the only exit path for deposited tokens, gates on the same flag:

```solidity
// contracts/L2/RsETHTokenWrapper.sol L120-L128
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

After `removeAllowedToken()` is called, every call to `withdraw()` or `withdrawTo()` for that asset reverts. The contract has no `recoverTokens`, `emergencyWithdraw`, or sweep function — it does not inherit `Recoverable`. There is no alternative exit path for the locked altRsETH tokens.

The identical pattern exists in `contracts/agETH/AGETHTokenWrapper.sol` at lines 111–119 and 157–160.

### Impact Explanation
All altRsETH tokens held in `RsETHTokenWrapper` at the time `removeAllowedToken()` is called become permanently frozen. Users holding `wrsETH` backed by those tokens can never redeem the underlying altRsETH. This is a **Critical** — permanent freezing of user funds.

### Likelihood Explanation
`TIMELOCK_ROLE` is a legitimate governance role expected to perform token lifecycle management (e.g., migrating from a deprecated bridge token to a new one). Calling `removeAllowedToken()` is a routine governance action with no on-chain warning that it will freeze all deposited collateral. The likelihood is **Medium**: the action is plausible during any token migration or deprecation event.

### Recommendation
Before setting `allowedTokens[_asset] = false`, verify that the contract holds zero balance of that token, or add a separate "deprecated" state that still permits withdrawals but blocks new deposits. Alternatively, add an admin-only `recoverTokens` function (similar to `contracts/utils/Recoverable.sol`) that can rescue tokens even when `allowedTokens` is false.

### Proof of Concept

1. Alice calls `deposit(altRsETH, 100e18)` on `RsETHTokenWrapper`. She receives 100 `wrsETH`. The contract now holds 100 altRsETH.
2. The `TIMELOCK_ROLE` calls `removeAllowedToken(altRsETH)` to migrate to a new token version.
3. Alice calls `withdraw(altRsETH, 100e18)`. The call reverts with `TokenNotAllowed` at line 121.
4. There is no other function Alice can call to recover her 100 altRsETH. The tokens are permanently frozen.

Relevant lines: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-119)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L157-160)
```text
    function removeAllowedToken(address _asset) external onlyRole(DEFAULT_ADMIN_ROLE) {
        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```
