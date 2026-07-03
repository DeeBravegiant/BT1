### Title
Removing an Allowed Token Permanently Freezes User Funds in Wrapper Contracts - (File: contracts/L2/RsETHTokenWrapper.sol, contracts/agETH/AGETHTokenWrapper.sol)

### Summary
Both `RsETHTokenWrapper` and `AGETHTokenWrapper` enforce the `allowedTokens` check symmetrically on both deposit and withdrawal. When governance removes a token via `removeAllowedToken`, all users who previously deposited that token and hold the corresponding wrapper tokens (`wrsETH`/`agETH`) are permanently unable to redeem their underlying assets, freezing those funds in the contract.

### Finding Description
In `RsETHTokenWrapper._deposit` and `RsETHTokenWrapper._withdraw`, the same `allowedTokens[_asset]` guard is applied: [1](#0-0) 

Both functions revert with `TokenNotAllowed()` if the asset is not in the allowlist. The `removeAllowedToken` function, callable by `TIMELOCK_ROLE`, sets `allowedTokens[_asset] = false` with no check on whether outstanding deposits exist: [2](#0-1) 

After removal, the underlying alt-rsETH tokens remain locked inside the wrapper contract. There is no emergency user-facing withdrawal path and no guard preventing removal when user balances are outstanding. The identical pattern exists in `AGETHTokenWrapper`: [3](#0-2) [4](#0-3) 

Note the contrast with `LRTConfig.removeSupportedAsset` on L1, which explicitly guards against removal when deposits are outstanding: [5](#0-4) 

No equivalent guard exists in either wrapper contract.

### Impact Explanation
Any user who deposited an alt-rsETH (or alt-agETH) token into the wrapper and holds `wrsETH` (or wrapped `agETH`) is unable to call `withdraw` or `withdrawTo` after the token is removed from the allowlist. Their underlying tokens are locked in the contract with no user-accessible recovery path. This constitutes a **permanent freezing of user funds** (Critical), or at minimum a **temporary freeze** (Medium) if governance re-adds the token — but re-adding a token removed for safety reasons defeats the purpose of the removal.

### Likelihood Explanation
The scenario is realistic: governance may legitimately want to deprecate a bridged alt-rsETH variant (e.g., due to a bridge exploit or token compromise) and call `removeAllowedToken`. The action is a normal governance operation, not an attack. Any user with a non-zero balance of the removed token's wrapper shares is immediately affected. The `TIMELOCK_ROLE` in `RsETHTokenWrapper` and `DEFAULT_ADMIN_ROLE` in `AGETHTokenWrapper` are the only prerequisites, both of which are expected to act in the normal protocol lifecycle.

### Recommendation
Remove the `allowedTokens` check from the withdrawal path. Deposits should remain gated by the allowlist (to prevent new exposure to a deprecated token), but withdrawals must always succeed so users can recover their funds:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    // No allowedTokens check here — users must always be able to exit
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

This mirrors the recommendation in the referenced Homora report: separate the permission model for deposits (gated) from withdrawals (always open). Apply the same fix to `AGETHTokenWrapper._withdraw`.

### Proof of Concept
1. `altRsETH` is an allowed token in `RsETHTokenWrapper`. Alice calls `deposit(altRsETH, 100e18)` → `_deposit` passes the `allowedTokens` check, transfers 100 `altRsETH` into the wrapper, mints 100 `wrsETH` to Alice. [6](#0-5) 
2. Governance (TIMELOCK_ROLE) calls `removeAllowedToken(altRsETH)` → `allowedTokens[altRsETH]` is set to `false`. [7](#0-6) 
3. Alice calls `withdraw(altRsETH, 100e18)` → `_withdraw` checks `allowedTokens[altRsETH]` → `false` → reverts with `TokenNotAllowed()`. [8](#0-7) 
4. Alice's 100 `altRsETH` tokens remain permanently locked inside `RsETHTokenWrapper`. Her 100 `wrsETH` tokens are worthless — they cannot be burned for any underlying asset.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-141)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }

    /// @notice Deposit tokens into the lockbox
    /// @param _asset The address of the token to deposit
    /// @param _to The address to send the XERC20 to
    /// @param _amount The amount of tokens to deposit
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L178-185)
```text
    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L155-160)
```text
    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(DEFAULT_ADMIN_ROLE) {
        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```
