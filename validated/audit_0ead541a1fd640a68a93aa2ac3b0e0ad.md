### Title
Cross-Token Arbitrage via Insufficiently Constrained Asset in `RsETHTokenWrapper` Deposit/Withdraw - (File: `contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`RsETHTokenWrapper` supports multiple allowed tokens (via `reinitialize` and `addAllowedToken`) and mints/burns `wrsETH` strictly 1:1 for any allowed token. Because no price accounting is performed, an attacker can deposit a lower-value allowed altRsETH token and withdraw a higher-value one, directly stealing funds from the wrapper's reserves.

---

### Finding Description

The `_deposit` and `_withdraw` internal functions each perform only a single guard: `if (!allowedTokens[_asset]) revert TokenNotAllowed()`. They then mint or burn `wrsETH` 1:1 against whichever allowed token the caller supplies. [1](#0-0) [2](#0-1) 

The contract explicitly supports multiple allowed tokens. The `reinitialize` function (callable by `DEFAULT_ADMIN_ROLE`) adds a second altRsETH token to the whitelist, and `addAllowedToken` (callable by `TIMELOCK_ROLE`) can add further tokens at any time. [3](#0-2) [4](#0-3) 

Both `deposit` and `withdraw` are unrestricted public entry points. [5](#0-4) [6](#0-5) 

When two allowed tokens trade at different market prices — a realistic condition during bridge migrations, bridge incidents, or liquidity imbalances — the 1:1 mint/burn invariant breaks. An attacker deposits the cheaper token and withdraws the more expensive one, extracting the price difference from the wrapper's reserves at the expense of honest depositors.

---

### Impact Explanation

**Critical — Direct theft of user funds at rest.**

Honest users who deposited the higher-value altRsETH token into the wrapper can have their collateral drained. The attacker receives more ETH-denominated value than they deposited, and the wrapper's reserves for the premium token are depleted. Affected users can no longer redeem their `wrsETH` for the token they originally deposited.

---

### Likelihood Explanation

**Medium.** The `reinitialize` function was specifically introduced to support a second altRsETH token, confirming this is an intended production deployment scenario. During any bridge migration window — when both the old and new altRsETH tokens are simultaneously allowed — the two tokens routinely trade at different prices on secondary markets. No privileged access is required to execute the attack once two tokens are whitelisted.

---

### Recommendation

1. **Enforce single-token invariant during transitions**: Remove the old token from `allowedTokens` before adding the new one, so at most one token is ever active at a time.
2. **Or, implement value-based accounting**: Track the ETH-denominated value of each deposited token (using an oracle) and issue/redeem `wrsETH` proportionally, rather than 1:1 by token count.
3. **At minimum**, add a check in `_withdraw` that the contract holds sufficient balance of the requested asset relative to the total `wrsETH` supply backed by that specific token, preventing cross-token redemption.

---

### Proof of Concept

Assume the wrapper holds 1,000 units of `altRsETH_A` (market price: 1.00 ETH each), backing 1,000 `wrsETH` in circulation. A second token `altRsETH_B` (market price: 0.95 ETH each) is added via `addAllowedToken`.

1. Attacker calls `deposit(altRsETH_B, 1000)`:
   - Transfers 1,000 `altRsETH_B` (worth ~950 ETH) into the wrapper.
   - Receives 1,000 `wrsETH` minted 1:1. [7](#0-6) 

2. Attacker calls `withdraw(altRsETH_A, 1000)`:
   - Burns 1,000 `wrsETH`.
   - Receives 1,000 `altRsETH_A` (worth ~1,000 ETH) transferred out. [8](#0-7) 

3. **Net profit**: ~50 ETH worth of value extracted from the wrapper in a single atomic sequence. The original depositors of `altRsETH_A` can no longer redeem their `wrsETH` for the token they deposited.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L69-71)
```text
    function deposit(address asset, uint256 _amount) external {
        _deposit(asset, msg.sender, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L84-86)
```text
    function withdraw(address asset, uint256 _amount) external {
        _withdraw(asset, msg.sender, _amount);
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
