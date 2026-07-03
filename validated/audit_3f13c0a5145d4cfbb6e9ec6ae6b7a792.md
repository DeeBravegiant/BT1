### Title
Cross-Token Withdrawal Drains Bridger Collateral and Permanently Freezes Depositor's Assets — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`_withdraw` accepts any `allowedToken` as the redemption asset without verifying it matches the token the caller deposited. Because wrsETH is a single fungible token with no per-asset accounting, a user who deposited tokenA can burn their wrsETH to claim tokenB (bridger collateral), leaving tokenA permanently irrecoverable in the wrapper.

---

### Finding Description

`_withdraw` performs only two checks before transferring tokens out:

1. `allowedTokens[_asset]` — the requested asset is in the allowed list
2. `_burn(msg.sender, _amount)` — the caller holds enough wrsETH [1](#0-0) 

There is no check that `_asset` is the same token the caller deposited, and no per-token deposit ledger exists anywhere in the contract. wrsETH is a single fungible ERC-20; it carries no information about which underlying token backed it.

`depositBridgerAssets` allows the bridger to deposit tokenB as collateral against the **total** wrsETH supply, computed by `maxAmountToDepositBridgerAsset`: [2](#0-1) 

This function returns `totalSupply() - tokenB.balanceOf(wrapper)`. When a user has already deposited tokenA (backing the existing wrsETH supply), the formula treats that wrsETH as unbacked by tokenB and allows the bridger to deposit tokenB against it — creating a state where both tokenA and tokenB are in the wrapper but only one set of wrsETH exists. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Concrete end state after the exploit:
- tokenA: N units locked in wrapper, unredeemable (wrsETH supply = 0)
- tokenB: drained to the attacker
- wrsETH supply: 0 (no token left to redeem tokenA)

tokenA has no recovery path: `_withdraw` requires burning wrsETH, but supply is zero. There is no admin rescue function, no emergency withdrawal, and no sweep mechanism in the contract. [1](#0-0) 

---

### Likelihood Explanation

**High.** The preconditions are entirely within normal operational flow:

- A second token being added via `addAllowedToken` / `reinitialize` is an explicitly supported upgrade path.
- The bridger depositing collateral via `depositBridgerAssets` is the documented legacy bridge flow.
- The user calling `withdraw(tokenB, N)` instead of `withdraw(tokenA, N)` requires no special role, no front-running, and no oracle manipulation — just knowledge that tokenB is in the wrapper.

No admin compromise, governance capture, or external dependency failure is required. [4](#0-3) [5](#0-4) 

---

### Recommendation

Two complementary fixes are needed:

1. **Per-token accounting in `_withdraw`**: Track `depositedBalance[token]` and require `depositedBalance[_asset] >= _amount` before transferring. Decrement on withdrawal.

2. **Fix `maxAmountToDepositBridgerAsset`**: Compute available capacity as `totalSupply() - sum_of_all_asset_balances()` rather than `totalSupply() - singleAsset.balanceOf(wrapper)`. This prevents the bridger from double-collateralizing wrsETH that is already backed by another asset.

Alternatively, if the design intent is that all allowed tokens are economically equivalent (all are alt-rsETH pegged 1:1 to canonical rsETH), document and enforce that the total of all asset balances must always equal `totalSupply()`, and gate `depositBridgerAssets` accordingly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Preconditions:
//   - tokenA and tokenB are both in allowedTokens
//   - bridger holds BRIDGER_ROLE
//   - userA holds tokenA

// Step 1: userA deposits tokenA
// wrapper.deposit(tokenA, N)  →  tokenA.balanceOf(wrapper) = N, wrsETH.totalSupply() = N

// Step 2: bridger deposits tokenB as collateral
// maxAmountToDepositBridgerAsset(tokenB) = totalSupply() - tokenB.balanceOf(wrapper)
//                                        = N - 0 = N  ✓ (passes CannotDeposit check)
// wrapper.depositBridgerAssets(tokenB, N)  →  tokenB.balanceOf(wrapper) = N

// Step 3: userA redeems wrsETH for tokenB (not tokenA)
// wrapper.withdraw(tokenB, N)
//   allowedTokens[tokenB] == true  ✓
//   _burn(userA, N)                ✓  (userA holds N wrsETH)
//   tokenB.transfer(userA, N)      ✓  (wrapper holds N tokenB)

// Final state:
//   tokenA.balanceOf(wrapper) = N   ← permanently frozen
//   tokenB.balanceOf(wrapper) = 0   ← drained
//   wrsETH.totalSupply()      = 0   ← no supply left to redeem tokenA
//
// Invariant broken: sum(tokenX.balanceOf(wrapper)) = N > totalSupply() = 0
// tokenA is irrecoverable.
```

The exploit requires no privileged role for the attacker. The bridger's `depositBridgerAssets` call is a normal operational action. The only non-user actor is the bridger (BRIDGER_ROLE), whose deposit is a prerequisite of the bridge design, not an attacker action. [1](#0-0) [3](#0-2) [2](#0-1)

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

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
