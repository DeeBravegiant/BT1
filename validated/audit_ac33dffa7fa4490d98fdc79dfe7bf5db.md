### Title
Cross-Token Arbitrage via Unchecked Output Token in `_withdraw` — (`File: contracts/L2/RsETHTokenWrapper.sol`)

### Summary

`RsETHTokenWrapper._withdraw` only verifies that the requested asset is in `allowedTokens` but does not enforce any per-token accounting or verify that the withdrawn token matches the deposited token. Because the contract supports multiple allowed altRsETH tokens and mints/burns wrsETH 1:1 for any of them, an attacker can deposit a lower-value allowed token and withdraw a higher-value allowed token, draining the contract of its more valuable reserves.

### Finding Description

`RsETHTokenWrapper` is designed to wrap multiple alternative rsETH tokens (altRsETH) from different L2 bridges into a single canonical wrsETH token. The contract maintains a flat `allowedTokens` mapping and mints/burns wrsETH at a strict 1:1 ratio for any allowed token.

The `_deposit` function: [1](#0-0) 

The `_withdraw` function: [2](#0-1) 

Both functions perform only a single check — `if (!allowedTokens[_asset]) revert TokenNotAllowed()` — with no per-token reserve tracking and no requirement that the withdrawn token match the deposited token. The contract already supports adding multiple tokens via `addAllowedToken` (TIMELOCK_ROLE) and the `reinitialize` function (which was already invoked to add a second altRsETH): [3](#0-2) [4](#0-3) 

Once two or more tokens are allowed, any user can:
1. Call `deposit(tokenA, amount)` — deposits a cheaper/depegged altRsETH, receives `amount` wrsETH.
2. Call `withdraw(tokenB, amount)` — burns `amount` wrsETH, receives the more valuable altRsETH token B.

There is no check that `tokenB` reserves are backed by `tokenB` deposits. The contract's total wrsETH supply is shared across all allowed tokens, so depositing token A implicitly grants a claim on token B's reserves.

### Impact Explanation

**Critical — Direct theft of user funds.**

An attacker can drain the contract's reserves of the more valuable altRsETH token by depositing a cheaper/depegged one. Users who originally deposited the higher-value token will be unable to withdraw it (permanent freezing of funds). The attack is atomic and requires no special privileges.

### Likelihood Explanation

**Medium.** The precondition is that at least two tokens are in `allowedTokens` with a price discrepancy. The `reinitialize(2)` function was already executed to add a second token, confirming the multi-token design is live. A price discrepancy between two bridge-wrapped rsETH tokens is realistic during bridge incidents, liquidity crises, or when one bridge version is deprecated. The attack path is fully permissionless — any external user can call `deposit` and `withdraw`.

### Recommendation

Track per-token reserves explicitly. Maintain a `mapping(address => uint256) public tokenReserves` that is incremented on `_deposit` and decremented on `_withdraw`. In `_withdraw`, revert if `tokenReserves[_asset] < _amount`. This ensures that withdrawals of a specific token are bounded by deposits of that same token, eliminating cross-token arbitrage.

Alternatively, restrict the contract to a single canonical token at any given time, or require that `withdraw` specifies the same token that was deposited (tracked per user).

### Proof of Concept

Assume two tokens are allowed: `tokenA` (depegged, worth 0.9 ETH per unit) and `tokenB` (canonical, worth 1.0 ETH per unit). The contract holds 1000 `tokenB` deposited by honest users.

1. Attacker acquires 1000 `tokenA` at market price (cost: ~900 ETH equivalent).
2. Attacker calls `deposit(tokenA, 1000)`:
   - `allowedTokens[tokenA]` → true ✓
   - Contract receives 1000 `tokenA`, mints 1000 wrsETH to attacker. [5](#0-4) 
3. Attacker calls `withdraw(tokenB, 1000)`:
   - `allowedTokens[tokenB]` → true ✓
   - Burns 1000 wrsETH, transfers 1000 `tokenB` to attacker. [6](#0-5) 
4. Attacker has profited ~100 ETH equivalent. Original `tokenB` depositors find the contract holds only `tokenA` and cannot withdraw their `tokenB`.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
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
