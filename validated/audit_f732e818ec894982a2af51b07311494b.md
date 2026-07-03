### Title
`removeAllowedToken` can be called while underlying token balance is still held in the wrapper, permanently freezing those assets - (File: contracts/L2/RsETHTokenWrapper.sol)

---

### Summary

`RsETHTokenWrapper.removeAllowedToken` sets `allowedTokens[_asset] = false` with no check on the contract's current balance of that asset. Because `_withdraw` gates every unwrap on `allowedTokens[_asset]`, any token A balance already deposited into the wrapper becomes permanently unreachable the moment the TIMELOCK_ROLE removes it.

---

### Finding Description

`RsETHTokenWrapper` is the L2 lockbox that lets users deposit an "alt-rsETH" token (e.g. a bridged variant) and receive the canonical `wrsETH` 1-for-1, and later burn `wrsETH` to reclaim the underlying alt-rsETH.

The privileged `removeAllowedToken` function simply flips the mapping entry to `false`:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  L180-185
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
``` [1](#0-0) 

There is no guard that verifies the contract holds zero balance of `_asset` before removal. The internal `_withdraw` path, however, unconditionally reverts if the flag is false:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  L120-121
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ...
}
``` [2](#0-1) 

Both public entry points `withdraw` and `withdrawTo` route through `_withdraw`, so every user-facing unwrap path is blocked. [3](#0-2) 

Contrast this with the pool-level `removeSupportedToken` implementations, which correctly guard on a zero balance before proceeding:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol  L598-599
if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
``` [4](#0-3) 

`RsETHTokenWrapper.removeAllowedToken` has no equivalent safeguard.

---

### Impact Explanation

Any alt-rsETH token balance held by the wrapper at the time of removal becomes permanently unwithdrawable. Users who deposited token A and hold `wrsETH` can no longer call `withdraw(tokenA, ...)` — the call reverts with `TokenNotAllowed`. The token A balance sits in the contract with no recovery path (there is no emergency-withdrawal function in `RsETHTokenWrapper`). This constitutes a **permanent freezing of user funds**.

Impact: **Critical — Permanent freezing of funds.** [5](#0-4) 

---

### Likelihood Explanation

The TIMELOCK_ROLE is a governance-controlled key, not a single EOA. A realistic trigger is a routine token migration or deprecation of an old bridged rsETH variant: the operator removes the old token from the allowed list before ensuring all depositors have unwrapped. No malicious intent is required — the missing balance check means a well-intentioned governance action silently freezes funds. The same pattern was confirmed as Medium severity in the reference report (Axelar XC20Wrapper) and the sponsor mitigated by removing the function entirely.

---

### Recommendation

Add a balance guard to `removeAllowedToken`, mirroring the pattern already used in the pool contracts:

```solidity
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
+   if (ERC20Upgradeable(_asset).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
```

This ensures the wrapper only permits removal when no user funds remain locked under that token, guaranteeing users can always unwrap before the token is deprecated. [6](#0-5) 

---

### Proof of Concept

1. TIMELOCK_ROLE deploys `RsETHTokenWrapper` with `altRsETH_v1` as an allowed token.
2. Alice calls `deposit(altRsETH_v1, 100e18)` → receives 100 `wrsETH`; the wrapper now holds 100 `altRsETH_v1`.
3. TIMELOCK_ROLE calls `removeAllowedToken(altRsETH_v1)` (e.g., to migrate to `altRsETH_v2`). No balance check exists; the call succeeds and sets `allowedTokens[altRsETH_v1] = false`.
4. Alice calls `withdraw(altRsETH_v1, 100e18)` → `_withdraw` hits `if (!allowedTokens[_asset]) revert TokenNotAllowed()` and reverts.
5. Alice's 100 `altRsETH_v1` tokens are permanently locked in the contract. Her `wrsETH` balance is burned only if the call succeeds, so she still holds `wrsETH` but cannot redeem it for `altRsETH_v1`. If no other allowed token has sufficient balance in the wrapper, her `wrsETH` is also effectively worthless. [5](#0-4) [1](#0-0)

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L596-605)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        delete tokenBridge[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
```
