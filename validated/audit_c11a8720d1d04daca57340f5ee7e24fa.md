Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Without Prior Price Update — (File: contracts/LRTDepositPool.sol)

## Summary
`LRTOracle` stores `rsETHPrice` as a persistent state variable updated only when `updateRSETHPrice()` is explicitly called. Both `depositETH()` and `depositAsset()` in `LRTDepositPool` compute the rsETH mint amount using this cached value without triggering a price refresh. When staking rewards accrue and the stored price is stale-low, new depositors receive more rsETH than their fair share, directly stealing unclaimed yield from existing holders.

## Finding Description
`rsETHPrice` is a storage variable in `LRTOracle` ( [1](#0-0) ) updated only inside `_updateRsETHPrice()`, which is invoked only when `updateRSETHPrice()` or `updateRSETHPriceAsManager()` is called explicitly ( [2](#0-1) ).

The deposit entry points never trigger a price update. `depositETH()` calls `_beforeDeposit()` which calls `getRsETHAmountToMint()`: [3](#0-2) 

`getRsETHAmountToMint()` reads the cached price directly: [4](#0-3) 

Similarly, `initiateWithdrawal()` calls `getExpectedAssetAmount()` which also reads the cached price: [5](#0-4) 

A grep across all `contracts/*.sol` confirms `updateRSETHPrice` is referenced only within `LRTOracle.sol` — it is never called from `LRTDepositPool` or `LRTWithdrawalManager`. The stale window is further extended by the `pricePercentageLimit` guard in `_updateRsETHPrice()`: if the price increase exceeds the configured threshold and the caller is not a manager, the public `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold`, preventing the price from being updated at all until a manager intervenes. [6](#0-5) 

## Impact Explanation
When rewards accrue (e.g., EigenLayer staking rewards), `totalETHInProtocol` grows but `rsETHPrice` remains at its last stored value. The mint formula `rsethAmountToMint = (amount * assetPrice) / rsETHPrice` over-issues rsETH because the denominator is stale-low. After `updateRSETHPrice()` is eventually called, the new depositor's inflated rsETH balance is worth more than they deposited — the excess value is extracted directly from the unclaimed yield belonging to existing rsETH holders. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
- `updateRSETHPrice()` is called off-chain by a keeper/bot, never atomically with deposits. A non-zero stale window always exists after any reward accrual event.
- No special privileges are required; any external caller can invoke `depositETH()` or `depositAsset()`.
- An attacker can observe a pending `updateRSETHPrice()` transaction in the mempool and front-run it with a large deposit at the stale price, maximizing the yield extracted.
- The `pricePercentageLimit` guard can cause `updateRSETHPrice()` to revert for non-managers, potentially extending the stale window significantly and increasing the exploitable amount. [7](#0-6) 

**Likelihood: Medium.**

## Recommendation
Refactor `_updateRsETHPrice()` so that a no-op update (price unchanged or within tolerance) does not revert. Then call this internal price refresh atomically at the start of `depositAsset()`, `depositETH()`, and `initiateWithdrawal()` before any mint/burn amount is computed. This eliminates the stale-price window entirely for all deposit and withdrawal operations.

## Proof of Concept
1. Protocol holds 1000 ETH TVL; 1000 rsETH minted; `rsETHPrice = 1e18`.
2. EigenLayer rewards accrue: TVL becomes 1010 ETH. True price = `1.01e18`. `updateRSETHPrice()` has not been called; `rsETHPrice` remains `1e18`.
3. Attacker calls `depositETH{value: 10 ether}(0, "")`. `getRsETHAmountToMint` computes `10e18 * 1e18 / 1e18 = 10 rsETH`. Fair amount at true price: `10e18 / 1.01e18 ≈ 9.9 rsETH`. Attacker receives ~0.1 extra rsETH.
4. `updateRSETHPrice()` is called. New TVL = 1020 ETH, total supply = 1010 rsETH, new price ≈ `1.0099e18`. Attacker's 10 rsETH is now worth ≈ 10.099 ETH — more than the 10 ETH deposited.
5. The ~0.099 ETH gain came directly from the 10 ETH of yield belonging to the original 1000 rsETH holders.

**Foundry fork test plan:** Fork mainnet, set `rsETHPrice` to a stale-low value (simulate reward accrual by increasing `totalETHInProtocol` without calling `updateRSETHPrice()`), call `depositETH` as an attacker, then call `updateRSETHPrice()`, and assert that `attacker.rsETHBalance * newRsETHPrice > depositedETH`. Verify the difference equals the yield extracted from existing holders by checking their proportional share before and after.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTDepositPool.sol (L86-87)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
