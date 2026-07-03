### Title
Late Depositors Steal Yield from Existing rsETH Holders via Stale `rsETHPrice` - (`contracts/LRTOracle.sol`)

---

### Summary

The `LRTOracle` stores `rsETHPrice` as a state variable that must be explicitly updated via `updateRSETHPrice()`. Between updates, staking rewards, MEV, and LST appreciation cause the protocol's TVL to grow. Because `LRTDepositPool.getRsETHAmountToMint()` uses the stale stored price, a depositor who deposits after rewards have accrued but before the price is updated receives more rsETH than they are entitled to. When the price is subsequently updated, the `_updateRsETHPrice()` function computes `previousTVL` using the **current** (inflated) supply at the **old** price, which absorbs the late depositor's contribution into the baseline and understates the reward amount for existing holders. The net effect is that the late depositor captures a portion of yield that belonged to existing rsETH holders.

---

### Finding Description

`LRTDepositPool.getRsETHAmountToMint()` calculates the rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` is a stored value that is only updated when `updateRSETHPrice()` is called explicitly. The deposit flow never triggers a price update before minting. [2](#0-1) 

Inside `_updateRsETHPrice()`, the previous TVL is computed as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
``` [3](#0-2) 

Here `rsethSupply` is the **current** total supply (already including any new deposits made at the stale price), while `rsETHPrice` is the **old** price. This means a late depositor's shares are silently folded into `previousTVL`, making the reward amount appear the same as it would have been without the deposit, while the new price is computed over a larger denominator — diluting existing holders.

The reward accrual path that creates the stale-price window includes:
- MEV/execution-layer rewards sent via `FeeReceiver.sendFunds()` to `LRTDepositPool`
- Continuous LST price appreciation reflected in `_getTotalEthInProtocol()`
- EigenLayer staking rewards tracked via `getEffectivePodShares()` [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Concrete example (ignoring protocol fee for clarity):

| Step | rsethSupply | totalETH | rsETHPrice |
|---|---|---|---|
| Initial | 100 | 100 ETH | 1.00 |
| Rewards accrue | 100 | 110 ETH | 1.00 (stale) |
| Attacker deposits 10 ETH at stale price | 110 | 120 ETH | 1.00 (stale) |
| `updateRSETHPrice()` called | 110 | 120 ETH | 120/110 ≈ **1.0909** |

- Attacker's 10 rsETH is now worth **10.909 ETH** — a gain of **0.909 ETH** from rewards they did not earn.
- Original 100 rsETH holders receive **109.09 ETH** instead of **110 ETH** — a loss of **0.91 ETH**.

Without the attacker's deposit, the correct price would be 110/100 = **1.10 ETH/rsETH**.

---

### Likelihood Explanation

`updateRSETHPrice()` is a public function callable by anyone. [2](#0-1) 

An attacker can:
1. Monitor `_getTotalEthInProtocol()` vs. `rsethSupply * rsETHPrice` to detect when rewards have accrued.
2. Call `depositETH()` or `depositAsset()` at the stale price.
3. Call `updateRSETHPrice()` themselves to lock in the benefit (or wait for the keeper to do so).

If the price increase exceeds `pricePercentageLimit`, only a manager can call `updateRSETHPriceAsManager()`, but the attacker's deposit at the stale price is already committed and will benefit when the manager eventually updates the price. [6](#0-5) 

The attack is profitable any time rewards have accrued and the price is stale — a condition that exists continuously between keeper updates.

---

### Recommendation

Call `_updateRsETHPrice()` (or an equivalent price refresh) at the beginning of `depositETH()` and `depositAsset()` before computing `getRsETHAmountToMint()`. This ensures every depositor pays the current fair price that already reflects all accrued rewards, eliminating the stale-price window.

Alternatively, compute `previousTVL` using a snapshot of `rsethSupply` taken at the time of the **last** price update (not the current supply), so that new deposits made at the stale price are not absorbed into the baseline.

---

### Proof of Concept

```
1. Deploy protocol; 100 ETH deposited → 100 rsETH minted; rsETHPrice = 1.0.
2. 10 ETH of staking rewards flow into LRTDepositPool via FeeReceiver.sendFunds().
   totalETHInProtocol = 110 ETH; rsETHPrice still = 1.0 (not yet updated).
3. Attacker calls depositETH(10 ETH):
   getRsETHAmountToMint = 10 * 1e18 / 1e18 = 10 rsETH minted.
   rsethSupply = 110; totalETH = 120.
4. Attacker calls updateRSETHPrice():
   previousTVL = 110 * 1.0 = 110 ETH
   rewardAmount = 120 - 110 = 10 ETH  ← same as without attacker
   newRsETHPrice = 120 / 110 ≈ 1.0909
5. Attacker redeems 10 rsETH → receives ≈ 10.909 ETH (profit ≈ 0.909 ETH).
6. Original 100 rsETH holders collectively receive ≈ 109.09 ETH instead of 110 ETH.
   Loss to original holders: ≈ 0.91 ETH stolen by attacker.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L234-234)
```text
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/FeeReceiver.sol (L53-57)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
```
