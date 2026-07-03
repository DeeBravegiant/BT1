### Title
Uninitialized `rsETHPrice` Denominator Causes Division-by-Zero, Temporarily Freezing All User Deposits — (`contracts/LRTDepositPool.sol` / `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a `uint256` state variable that defaults to `0` and is only updated when `updateRSETHPrice()` is explicitly called. `LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()` with no guard against a zero denominator. If `updateRSETHPrice()` has never been called (e.g., fresh deployment, or after a redeployment of the oracle), every call to `depositETH()` and `depositAsset()` reverts, temporarily freezing all user deposits.

---

### Finding Description

`LRTOracle` declares `rsETHPrice` as a plain `uint256` storage variable with no initializer: [1](#0-0) 

Its value is only written inside `_updateRsETHPrice()`, which is reached through the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. Until one of those is called, `rsETHPrice == 0`. [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` uses `rsETHPrice` as the denominator of the mint-rate calculation with no zero-check: [3](#0-2) 

This function is called unconditionally by the internal `_beforeDeposit()`: [4](#0-3) 

Which is invoked by both public deposit entry points: [5](#0-4) [6](#0-5) 

When `rsETHPrice == 0`, the Solidity 0.8 runtime reverts the division, causing every deposit attempt to fail.

The `initialize()` function of `LRTOracle` never seeds `rsETHPrice`: [7](#0-6) 

`_updateRsETHPrice()` does set `rsETHPrice = 1 ether` when `rsethSupply == 0`, but only after it is explicitly called: [8](#0-7) 

There is no enforcement that `updateRSETHPrice()` must be called before deposits are opened, and no guard in `getRsETHAmountToMint()` against a zero price.

---

### Impact Explanation

All calls to `depositETH()` and `depositAsset()` revert with a division-by-zero panic whenever `rsETHPrice == 0`. This temporarily freezes the entire deposit surface of the protocol — no user can mint rsETH — until an operator manually calls `updateRSETHPrice()`. This matches the **Medium — Temporary freezing of funds** impact class.

---

### Likelihood Explanation

The window exists in every fresh deployment or oracle redeployment before `updateRSETHPrice()` is called. Because `updateRSETHPrice()` is not called atomically in `initialize()`, there is always a non-zero gap during which deposits are bricked. An unsuspecting user who deposits in this window will have their transaction revert. The condition is also reproducible any time the oracle contract is upgraded or redeployed without an immediate price update.

---

### Recommendation

Initialize `rsETHPrice` to `1 ether` inside `LRTOracle.initialize()`, mirroring the logic already present in `_updateRsETHPrice()` for the zero-supply case:

```solidity
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    rsETHPrice = 1 ether;          // ← add this
    highestRsethPrice = 1 ether;   // ← add this
    emit UpdatedLRTConfig(lrtConfigAddr);
}
```

Additionally, add a defensive guard in `getRsETHAmountToMint()`:

```solidity
uint256 price = lrtOracle.rsETHPrice();
require(price > 0, "rsETHPrice not initialized");
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / price;
```

---

### Proof of Concept

1. Deploy `LRTOracle` and `LRTDepositPool` (fresh deployment; `updateRSETHPrice()` not yet called).
2. `LRTOracle.rsETHPrice()` returns `0`.
3. Any user calls `depositETH{value: 1 ether}(0, "")`.
4. Execution reaches `getRsETHAmountToMint()` → `(1e18 * assetPrice) / 0` → Solidity 0.8 division-by-zero panic → revert.
5. All deposits are frozen until an operator calls `updateRSETHPrice()`.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTDepositPool.sol (L87-87)
```text
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L111-111)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-665)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```
