### Title
Uncollateralized `mint()` Creates Withdrawal Revert Window — (`contracts/agETH/AGETHTokenWrapper.sol`)

---

### Summary

`AGETHTokenWrapper` allows a `MINTER_ROLE` address to mint wrapper shares via `mint()` without depositing any `altAgETH` collateral. A user who receives those shares and immediately calls `withdraw()` will receive a revert from `safeTransfer` because the contract holds no `altAgETH`. Funds are temporarily frozen until a `BRIDGER_ROLE` address calls `depositBridgerAssets()`.

---

### Finding Description

`mint()` mints ERC-20 shares with no collateral requirement: [1](#0-0) 

`_withdraw()` burns shares first, then attempts `safeTransfer`: [2](#0-1) 

If `ERC20Upgradeable(_asset).balanceOf(address(this)) < _amount`, the `safeTransfer` on line 116 reverts. Because the burn and transfer are in the same transaction, the burn is also reverted — the user's shares are not destroyed, but the withdrawal fails.

The contract's own comment on `depositBridgerAssets` confirms the intended two-step flow: [3](#0-2) 

There is no on-chain enforcement that collateral must be present before or at the time of minting. The gap between `mint()` and `depositBridgerAssets()` is unbounded.

---

### Impact Explanation

Any user who holds wrapper shares minted via `mint()` (the bridge path) cannot redeem them for `altAgETH` until the bridger deposits sufficient collateral. The duration of the freeze is entirely at the discretion of the off-chain bridger operator. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

This is the normal operating path for the bridge flow: shares are minted on L2 when a bridge message arrives, and the bridger separately deposits the backing tokens. Every bridge event creates this window. No adversarial action is required — the freeze is a structural consequence of the design.

---

### Recommendation

Enforce collateral-before-mint ordering, or add a check in `_withdraw` that gracefully handles undercollateralization (e.g., queue the withdrawal). At minimum, document the maximum acceptable delay and enforce it on-chain (e.g., a deadline after which the minted shares can be burned without collateral as a safety valve).

---

### Proof of Concept

```solidity
// 1. MINTER_ROLE mints shares to Alice with zero altAgETH in the contract
wrapper.mint(alice, 1e18);

// 2. Alice immediately tries to withdraw
vm.prank(alice);
wrapper.withdraw(altAgETH, 1e18);
// → reverts: ERC20: transfer amount exceeds balance
// Alice's shares are intact but she cannot access her funds

// 3. Only after BRIDGER_ROLE deposits does withdrawal succeed
vm.prank(bridger);
altAgETHToken.approve(address(wrapper), 1e18);
wrapper.depositBridgerAssets(altAgETH, 1e18);

vm.prank(alice);
wrapper.withdraw(altAgETH, 1e18); // succeeds
```

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-119)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L138-151)
```text
    /// @dev Legacy function - Deposit for when the agETH is bridged by the
    /// bridger from L1 so as to collateralize already minted agETH on L2
    ///
    /// @param _asset The address of the token to deposit
    /// @param _amount The amount of tokens to deposit
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L165-167)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
