### Title
Attacker Can Steal Higher-Value rsETH Variant From `RsETHTokenWrapper` By Depositing a Lower-Value Allowed Token — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`RsETHTokenWrapper` (wrsETH) accepts multiple "allowed" rsETH-variant tokens and mints/burns wrsETH 1:1 for any of them. Because the contract never tracks *which* token a user deposited, any holder of wrsETH can burn it to withdraw *any* allowed token held by the contract. When two allowed tokens trade at different prices, an attacker deposits the cheaper token, then withdraws the more expensive one, stealing the difference from honest depositors.

---

### Finding Description

`RsETHTokenWrapper` is deployed on L2 chains as a lockbox that wraps different rsETH representations (e.g., a LayerZero OFT rsETH and an alternative bridge rsETH) into a single `wrsETH` ERC-20.

The contract supports multiple allowed tokens, added via `initialize` and `reinitialize`:

```solidity
// contracts/L2/RsETHTokenWrapper.sol
function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
    _addAllowedToken(_altRsETH);
}
``` [1](#0-0) 

Deposit mints wrsETH 1:1 for any allowed token:

```solidity
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    _mint(_to, _amount);
    ...
}
``` [2](#0-1) 

Withdrawal burns wrsETH 1:1 and transfers **any** allowed token the caller names:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    ...
}
``` [3](#0-2) 

Both `deposit` and `withdraw` are public with no access control:

```solidity
function deposit(address asset, uint256 _amount) external { _deposit(asset, msg.sender, _amount); }
function withdraw(address asset, uint256 _amount) external { _withdraw(asset, msg.sender, _amount); }
``` [4](#0-3) 

There is **no per-token accounting** and **no value equivalence check**. The contract assumes all allowed tokens are permanently 1:1, but this assumption is violated whenever the two rsETH variants trade at different prices on secondary markets (a routine occurrence due to bridge latency, liquidity differences, or depeg events).

---

### Impact Explanation

**Critical — Direct theft of user funds.**

If `rsETH_OFT` (Token A) trades at 1.05 ETH and `altRsETH` (Token B) trades at 1.00 ETH:

1. Attacker acquires 100 Token B (cost: 100 ETH).
2. Calls `deposit(tokenB, 100e18)` → receives 100 wrsETH.
3. Calls `withdraw(tokenA, 100e18)` → receives 100 Token A (worth 105 ETH).
4. Net profit: 5 ETH, extracted from honest Token A depositors.

Victims who deposited Token A are left with Token B (the cheaper variant) when they later withdraw. The attack scales linearly with the price spread and the Token A balance held by the wrapper.

---

### Likelihood Explanation

**High.** The `reinitialize` function explicitly adds a second allowed token, confirming the multi-token design is intentional and deployed. rsETH bridge variants routinely trade at small but non-zero spreads on L2 DEXes. The attack requires no special permissions, no flash loan, and no oracle manipulation — only a price difference between two tokens that the contract already supports.

---

### Recommendation

Track per-token balances and enforce that a withdrawal of Token A can only be satisfied by wrsETH that was minted from Token A deposits. Alternatively, enforce a strict 1:1 peg check at withdrawal time using a trusted oracle, and revert if the requested token's on-chain price deviates from the deposited token's price beyond a tight tolerance. The simplest safe design is to allow only a single underlying token per wrapper instance.

---

### Proof of Concept

```solidity
// Preconditions:
//   - wrapper has two allowed tokens: rsETH_OFT (tokenA) and altRsETH (tokenB)
//   - tokenA is worth 1.05 ETH, tokenB is worth 1.00 ETH
//   - wrapper holds 100e18 tokenA (deposited by honest users)
//   - attacker holds 100e18 tokenB

// Step 1: attacker deposits the cheaper token
tokenB.approve(address(wrapper), 100e18);
wrapper.deposit(address(tokenB), 100e18);
// attacker now holds 100e18 wrsETH

// Step 2: attacker withdraws the more expensive token
wrapper.withdraw(address(tokenA), 100e18);
// attacker now holds 100e18 tokenA (worth 105 ETH)
// attacker spent 100e18 tokenB (worth 100 ETH)
// net gain: 5 ETH stolen from honest tokenA depositors

// Honest users who deposited tokenA now find only tokenB in the wrapper
// when they call withdraw(tokenA, ...) it reverts with insufficient balance
```

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L69-86)
```text
    function deposit(address asset, uint256 _amount) external {
        _deposit(asset, msg.sender, _amount);
    }

    /// @dev Deposit altRsETH for wrsETH to a user
    /// @param asset The address of the token to deposit
    /// @param _to The user to send the XERC20 to
    /// @param _amount The amount of tokens to deposit
    function depositTo(address asset, address _to, uint256 _amount) external {
        _deposit(asset, _to, _amount);
    }

    /// @dev Withdraw altRseth tokens from wrsETH
    /// @param asset The address of the token to withdraw
    /// @param _amount The amount of tokens to withdraw
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
