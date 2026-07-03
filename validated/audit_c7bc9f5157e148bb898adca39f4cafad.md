### Title
Stale `rsETHPrice` in `instantWithdrawal` Allows Exit at Pre-Slashing Price, Socializing Loss to Remaining Holders - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager.instantWithdrawal` computes the asset payout using the **stored** `rsETHPrice` from `LRTOracle`, which is only updated when `updateRSETHPrice()` is explicitly called. After an EigenLayer slashing event reduces the protocol's backing ETH, the stored price remains stale (too high) until `updateRSETHPrice()` is called. Any rsETH holder can exploit this window by calling `instantWithdrawal` at the inflated stale price, then re-depositing after the price is updated downward — extracting value from remaining holders who bear the full loss.

### Finding Description

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` (public) or `updateRSETHPriceAsManager()` is called. It is not updated atomically with on-chain state changes in EigenLayer.

`instantWithdrawal` computes the payout via `getExpectedAssetAmount`:

```solidity
// LRTWithdrawalManager.sol:228
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

```solidity
// LRTWithdrawalManager.sol:593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

This reads the **stored** `lrtOracle.rsETHPrice()` directly — no freshness check, no call to `updateRSETHPrice()`. The payout is then immediately transferred:

```solidity
// LRTWithdrawalManager.sol:250
_transferAsset(asset, msg.sender, userAmount);
```

Contrast this with the regular withdrawal path (`unlockQueue` → `_calculatePayoutAmount`), which applies `min(expectedAssetAmount, currentReturn)` — so if the price has dropped, users receive the lower current value. `instantWithdrawal` has no such protection; it pays out based entirely on the stale stored price.

**Attack sequence:**

1. An EigenLayer slashing event occurs, reducing the ETH backing rsETH. The true rsETH value per share drops, but `rsETHPrice` in `LRTOracle` is still the old (higher) value.
2. The attacker calls `instantWithdrawal(asset, rsETHAmount, ...)` — burning rsETH and receiving assets calculated at the stale high price.
3. The attacker (or anyone) calls `updateRSETHPrice()` — the price drops to reflect the slashing.
4. The attacker calls `depositETH` / `depositAsset` at the new lower price, receiving more rsETH than they burned in step 2.
5. Net result: the attacker has the same rsETH balance but extracted real assets from the protocol, socializing the loss to remaining holders.

The attacker can bundle steps 2–4 atomically: `instantWithdrawal → updateRSETHPrice → deposit`, directly analogous to the Frankencoin `redeem + end + deposit` bundle.

### Impact Explanation

The attacker extracts real ETH/LST value from the protocol at the expense of remaining rsETH holders, who now hold shares backed by fewer assets. This is a direct transfer of value from passive holders to the attacker. The magnitude scales with the slashing amount and the attacker's rsETH position. This constitutes **theft of user funds** (High/Critical depending on slashing magnitude).

### Likelihood Explanation

EigenLayer slashing is a known, anticipated risk for restaking protocols. The attack window is the time between a slashing event becoming visible on-chain and `updateRSETHPrice()` being called. Since `updateRSETHPrice()` is called off-chain by bots (not atomically with slashing), this window is non-zero and observable. Any rsETH holder can execute this without special permissions.

### Recommendation

1. **Force a price update before payout in `instantWithdrawal`:** Call `ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice()` at the start of `instantWithdrawal` to ensure the price is fresh before computing the payout.
2. **Amortize losses:** Rather than allowing the price to drop instantly, implement a loss-amortization mechanism (similar to the Frankencoin recommendation) that spreads the price decrease over multiple periods.
3. **Add a slippage/minimum-price guard:** Allow callers to specify a `minAssetAmount` and revert if the computed payout falls below it, preventing exploitation of stale prices in either direction.

### Proof of Concept

```
State: rsETHPrice = 1.05e18 (stale, pre-slashing)
True backing after slashing: 1.00e18 per rsETH

Attacker holds 100e18 rsETH.

Step 1: instantWithdrawal(ETH, 100e18)
  assetAmountUnlocked = 100e18 * 1.05e18 / 1e18 = 105 ETH  ← stale price used
  Attacker receives 105 ETH (minus fee), burns 100e18 rsETH

Step 2: updateRSETHPrice()
  rsETHPrice updated to 1.00e18

Step 3: depositETH{value: 105 ETH}()
  rsethAmountToMint = 105e18 * 1e18 / 1.00e18 = 105e18 rsETH

Net: Attacker started with 100e18 rsETH, ends with 105e18 rsETH.
Remaining holders: their rsETH is now backed by 5 ETH less than before.
```

The root cause is at: [1](#0-0) 

which calls: [2](#0-1) 

using the stale stored price from: [3](#0-2) 

The public entry point that allows anyone to trigger the price update after the fact is: [3](#0-2) 

The contrast with the protected regular withdrawal path (which uses `min`) is at: [4](#0-3)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```
