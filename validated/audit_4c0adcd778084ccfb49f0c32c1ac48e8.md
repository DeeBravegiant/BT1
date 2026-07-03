### Title
Front-running `updateRSETHPrice()` Allows Theft of Accrued Yield from Existing rsETH Holders - (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function that causes a lump-sum jump in the stored `rsETHPrice` whenever it is called. An attacker can deposit ETH/LST at the stale (lower) price immediately before this update, receive more rsETH than fair value, and then sell that rsETH on secondary markets after the price jumps — stealing a proportional share of the accrued yield that should have belonged to existing rsETH holders.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a state variable that is only updated when `updateRSETHPrice()` is explicitly called: [1](#0-0) 

This function is fully public with no access control. Between calls, EigenLayer staking rewards and LST appreciation cause the true backing value of rsETH to increase, but the stored `rsETHPrice` remains stale. When `updateRSETHPrice()` is finally called, `rsETHPrice` jumps in a single transaction: [2](#0-1) [3](#0-2) 

`LRTDepositPool.getRsETHAmountToMint()` uses this stored price directly to compute how many rsETH tokens a depositor receives: [4](#0-3) 

Because `rsETHPrice` is stale (lower than the true value), a depositor who acts before the update receives **more rsETH than their deposit is worth at fair value**. After the price update, that rsETH can be sold on secondary markets (e.g., Curve, Uniswap) at the new higher price, extracting value from existing holders.

The `pricePercentageLimit` guard only triggers when `newRsETHPrice > highestRsethPrice`: [5](#0-4) 

This means the guard is entirely absent during price recovery (when the current price is below the all-time high), and even when active it only caps the per-update jump — it does not prevent the front-run attack within the allowed jump window. Additionally, if `pricePercentageLimit` is set to `0`, there is no cap at all.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Let `T` = total ETH in protocol at the stale price, `T'` = true total ETH (including accrued rewards, `T' > T`), `S` = current rsETH supply, `D` = attacker deposit.

- Attacker deposits `D` ETH at stale price `T/S`, receiving `D·S/T` rsETH.
- After the price update, the new price is approximately `(T'+D)/(S + D·S/T)`.
- Attacker's rsETH is worth `D·(T'+D)/(T+D)` ETH.
- **Attacker profit = `D·(T'−T)/(T+D)`**, which is always positive whenever rewards have accrued (`T' > T`).

For a large deposit `D ≫ T`, the attacker captures nearly the entire accrued reward `T'−T`, directly at the expense of existing rsETH holders whose yield is diluted.

---

### Likelihood Explanation

**Medium-High.** The attack requires no special permissions — only capital. The attacker can:

1. Monitor on-chain conditions (time since last `updateRSETHPrice()` call, EigenLayer reward accrual) to estimate the pending price jump.
2. Front-run any pending `updateRSETHPrice()` transaction in the mempool, or simply call it themselves after depositing.
3. Sell rsETH on a DEX immediately after the price update — no withdrawal delay is required.

The attack is repeatable every reward cycle. The only cost is the deposit capital and gas.

---

### Recommendation

1. **Adopt a gradual release model** (analogous to Synthetix `StakingRewards`): instead of applying the full accrued reward in one `updateRSETHPrice()` call, stream the price increase over a fixed window (e.g., 24 hours). This eliminates the exploitable lump-sum surge.

2. **Alternatively, add a deposit cooldown or commit-reveal delay** so that deposits made within a short window before a price update do not benefit from that update.

3. **Ensure `pricePercentageLimit` is always non-zero** and covers the recovery-from-dip case (compare against `rsETHPrice`, not only `highestRsethPrice`).

---

### Proof of Concept

**Setup:** Assume `rsETHPrice = 1.05e18` (stale), true backing = `1.06e18` (rewards accrued but not yet reflected). `pricePercentageLimit = 0` (no cap).

**Step 1 — Attacker deposits 10,000 ETH at stale price:**
```
rsethAmountToMint = (10_000e18 * 1e18) / 1.05e18 ≈ 9523.8 rsETH
``` [6](#0-5) 

**Step 2 — Attacker calls `updateRSETHPrice()`:**
- `totalETHInProtocol` now includes accrued rewards → `rsETHPrice` jumps to ~`1.06e18`. [1](#0-0) 

**Step 3 — Attacker sells 9523.8 rsETH on a DEX at the new rate:**
```
ETH received ≈ 9523.8 * 1.06e18 / 1e18 ≈ 10,095 ETH
Profit ≈ 95 ETH stolen from existing holders' accrued yield
```

The profit scales linearly with deposit size and with the magnitude of the accrued-but-unreflected reward. Existing rsETH holders receive proportionally less yield than they earned.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
