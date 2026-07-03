### Title
`RsETHTokenWrapper` incorrectly assumes a 1:1 mapping between all allowed altRsETH tokens and wrsETH, enabling cross-token arbitrage theft - (File: contracts/L2/RsETHTokenWrapper.sol)

---

### Summary

`RsETHTokenWrapper` supports multiple "alternative rsETH" tokens via an `allowedTokens` mapping and mints/burns `wrsETH` at a strict 1:1 ratio with any of them. When more than one altRsETH token is active, any user can deposit the cheaper token, receive wrsETH 1:1, then immediately withdraw the more valuable token 1:1 — draining value from other depositors. Additionally, `maxAmountToDepositBridgerAsset` compares the **total** wrsETH supply against the balance of only **one** specific altRsETH token, producing a systematically wrong ceiling once multiple tokens are in play.

---

### Finding Description

`RsETHTokenWrapper` is designed to wrap "alternative rsETH tokens" on L2 chains into a canonical `wrsETH`. The contract explicitly supports multiple allowed tokens:

- `initialize` registers the first altRsETH token.
- `reinitialize` (admin-gated) adds a second altRsETH token.
- `addAllowedToken` (timelock-gated) can add further tokens.

The internal `_deposit` function mints exactly `_amount` of wrsETH for exactly `_amount` of any allowed altRsETH:

```solidity
// contracts/L2/RsETHTokenWrapper.sol:134-141
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    _mint(_to, _amount);
    emit Deposit(_asset, msg.sender, _to, _amount);
}
```

The internal `_withdraw` function burns exactly `_amount` of wrsETH and transfers exactly `_amount` of any allowed altRsETH:

```solidity
// contracts/L2/RsETHTokenWrapper.sol:120-128
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

No exchange rate is consulted. The caller of `withdraw` freely chooses **which** allowed altRsETH to receive. If two allowed tokens trade at different prices (e.g., because they originate from different bridges, or because one is a rebasing variant), the wrapper becomes a zero-cost swap venue between them.

`maxAmountToDepositBridgerAsset` compounds the problem:

```solidity
// contracts/L2/RsETHTokenWrapper.sol:99-110
function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
    uint256 wrsETHSupply = totalSupply();          // ALL wrsETH, from ALL altRsETH tokens
    uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
    if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
    return wrsETHSupply - balanceOfAssetInWrapper; // wrong: counts wrsETH minted for other tokens
}
```

When two tokens are active (e.g., 100 wrsETH minted for altRsETH_A, 50 wrsETH minted for altRsETH_B), querying for altRsETH_A returns `150 − 100 = 50`, implying 50 more altRsETH_A can be bridged in — but those 50 wrsETH were already issued against altRsETH_B. The ceiling is inflated by the supply of every other allowed token, breaking the collateralisation invariant.

The identical pattern exists in `contracts/agETH/AGETHTokenWrapper.sol` at lines 90–101, 111–119, and 125–131.

---

### Impact Explanation

**Critical — direct theft of user funds.**

Any unprivileged user holding wrsETH can call `withdraw(altRsETH_B, amount)` to receive the more valuable token regardless of which token they originally deposited. The value difference is extracted from other depositors who contributed the more valuable token. Because the public `withdraw` and `withdrawTo` functions impose no restriction on which allowed asset is selected, the attack requires no special role and no flash loan.

---

### Likelihood Explanation

**Medium.**

The protocol explicitly provisions for multiple allowed tokens: `reinitialize` is present in the deployed contract and is the documented upgrade path for adding a second altRsETH. Once a second token is added (a routine governance action, not an attack), the vulnerability is immediately exploitable by any user. Different altRsETH tokens originating from different bridges (LayerZero OFT vs. native bridge) routinely trade at small but non-zero discounts/premiums on secondary markets, providing the price differential needed to profit.

---

### Recommendation

1. **Track per-token accounting**: maintain a `mapping(address => uint256) public wrsETHMintedFor` that records how much wrsETH was minted against each altRsETH token. Use this per-token balance (not `totalSupply()`) in `maxAmountToDepositBridgerAsset`.
2. **Enforce same-token withdrawal**: require that wrsETH can only be redeemed for the same altRsETH token it was minted against, or introduce an oracle-based exchange rate between allowed tokens before permitting cross-token redemption.
3. Apply the same fix to `AGETHTokenWrapper`.

---

### Proof of Concept

**Setup**: Admin calls `reinitialize(altRsETH_B)` to add a second allowed token alongside the original `altRsETH_A`. Suppose `altRsETH_B` trades at 1.05× the value of `altRsETH_A` (a realistic bridge premium).

**Step 1 — Victim deposits the valuable token:**
```
Alice calls deposit(altRsETH_B, 100e18)
→ wrapper receives 100e18 altRsETH_B
→ Alice receives 100e18 wrsETH
```

**Step 2 — Attacker deposits the cheap token:**
```
Bob calls deposit(altRsETH_A, 100e18)
→ wrapper receives 100e18 altRsETH_A
→ Bob receives 100e18 wrsETH
```

**Step 3 — Attacker withdraws the valuable token:**
```
Bob calls withdraw(altRsETH_B, 100e18)   // ← freely chooses altRsETH_B
→ burns 100e18 wrsETH
→ Bob receives 100e18 altRsETH_B  (worth 105e18 altRsETH_A equivalent)
```

**Result**: Bob spent 100e18 altRsETH_A and received 100e18 altRsETH_B, netting a 5 ETH-equivalent profit. Alice's 100e18 altRsETH_B is gone; the wrapper now holds only 100e18 altRsETH_A against Alice's 100e18 wrsETH, which she cannot fully redeem at fair value.

The root cause is the unconditional 1:1 mint/burn in `_deposit`/`_withdraw` with no per-token accounting and no exchange rate check. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L90-101)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrapped agETH minted
        uint256 agETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > agETHSupply) return 0;

        return agETHSupply - balanceOfAssetInWrapper;
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
