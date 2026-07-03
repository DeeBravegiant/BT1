### Title
`_updateRsETHPrice` Downside Protection Returns Early Without Updating `rsETHPrice`, Leaving Stale Inflated Price After Unpause - (File: contracts/LRTOracle.sol)

---

### Summary

In `LRTOracle._updateRsETHPrice()`, when the downside protection path triggers, the function pauses all three contracts and immediately returns — without ever writing the newly computed `newRsETHPrice` back to `rsETHPrice`. The stale, higher price persists in storage. After the admin unpauses the protocol, any call to `LRTWithdrawalManager.instantWithdrawal()` (or `unlockQueue()`) that races ahead of `updateRSETHPrice()` will use the inflated stale price, allowing users to extract more assets per rsETH burned than the current TVL supports.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` contains a downside-protection block at lines 270–282:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;          // ← exits before rsETHPrice is written
}
``` [1](#0-0) 

The function computes `newRsETHPrice` earlier in the same call, but the assignment `rsETHPrice = newRsETHPrice` lives at line 313, which is never reached when the early return fires: [2](#0-1) 

After the early return, `rsETHPrice` still holds the pre-drop (higher) value. `updateRSETHPrice()` carries a `whenNotPaused` guard, so the price cannot be corrected while the oracle is paused: [3](#0-2) 

Once the admin unpauses the protocol, there is a window — before anyone calls `updateRSETHPrice()` — during which `rsETHPrice` is stale and inflated. `LRTWithdrawalManager.instantWithdrawal()` reads `rsETHPrice` directly and transfers assets immediately:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
// getExpectedAssetAmount: amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)
``` [4](#0-3) [5](#0-4) 

Because `rsETHPrice` is inflated, `assetAmountUnlocked` is larger than the current TVL warrants. The user burns rsETH and receives excess assets with no recalculation step.

The same stale price is consumed by `unlockQueue()` via `_createUnlockParams()`: [6](#0-5) 

Although `unlockQueue()` is operator-restricted, `instantWithdrawal()` is open to any user.

---

### Impact Explanation

**High — theft of funds.** A user calling `instantWithdrawal()` immediately after the unpause burns rsETH and receives assets priced at the stale (pre-drop) `rsETHPrice`. The excess assets come from the protocol's TVL, diluting all remaining rsETH holders. The magnitude equals `(stalePriceRatio - 1) × rsETHBurned × assetPrice`, which can be significant when the price drop that triggered the protection was large.

---

### Likelihood Explanation

**Low–Medium.** Three conditions must align:

1. TVL drops by more than `pricePercentageLimit` in a single `updateRSETHPrice()` call (e.g., a large slashing event or oracle manipulation).
2. The admin subsequently unpauses the protocol.
3. An attacker front-runs the `updateRSETHPrice()` transaction that would correct the price.

Conditions 1 and 2 are realistic operational events. Condition 3 is a straightforward mempool front-run with no special privileges required. Instant withdrawal must be enabled for the target asset, but this is a normal operational configuration.

---

### Recommendation

Update `rsETHPrice` to `newRsETHPrice` **before** calling `_pause()` and returning in the downside-protection block. This ensures the stored price always reflects the actual current state, even when the circuit-breaker fires:

```solidity
if (isPriceDecreaseOffLimit) {
    rsETHPrice = newRsETHPrice;          // ← add this line
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    emit RsETHPriceUpdate(rsETHPrice, previousPrice);
    return;
}
```

This mirrors the fix applied to IonPool (PR #27): ensure the intended state update executes before the pause takes effect.

---

### Proof of Concept

1. TVL drops sharply (e.g., EigenLayer slashing). `pricePercentageLimit` is set to 1 % (1e16).
2. Anyone calls `LRTOracle.updateRSETHPrice()`.
3. Inside `_updateRsETHPrice()`, `newRsETHPrice` is computed as, say, 0.97 ETH while `rsETHPrice` is 1.00 ETH. `isPriceDecreaseOffLimit` is `true`.
4. The function pauses `lrtDepositPool`, `withdrawalManager`, and the oracle, then returns. `rsETHPrice` remains at 1.00 ETH.
5. Admin calls `unpause()` on all three contracts.
6. Attacker immediately calls `LRTWithdrawalManager.instantWithdrawal(ETH, 100e18, "")`.
7. `getExpectedAssetAmount` returns `100e18 * 1.00e18 / 1e18 = 100 ETH` instead of the correct `97 ETH`.
8. Attacker receives 100 ETH, burning only 100 rsETH worth 97 ETH at current prices — a 3 ETH gain at the expense of remaining holders.
9. Attacker repeats until `updateRSETHPrice()` is called and the price is corrected.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L846-849)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
```
