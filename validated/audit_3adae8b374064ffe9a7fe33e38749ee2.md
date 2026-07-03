Audit Report

## Title
Stale `rsETHPrice` Allows Deposits at Below-Fair-Value Price, Stealing Yield from rsETH Holders - (File: contracts/LRTOracle.sol)

## Summary

`LRTOracle.updateRSETHPrice()` carries no access control beyond `whenNotPaused`, allowing any address to call it. Deposits in `LRTDepositPool` mint rsETH using the last-written `rsETHPrice` storage variable rather than a freshly computed price. When rewards accrue in EigenLayer strategies between price updates, the stored price is stale (below fair value), and any depositor during this window receives more rsETH than they are entitled to — extracting yield that belongs to existing rsETH holders.

## Finding Description

**Root cause 1 — `updateRSETHPrice()` is permissionlessly callable:**

`LRTOracle.sol` line 87 exposes `updateRSETHPrice()` as `public whenNotPaused` with no role check. Any EOA or contract may call it at will. [1](#0-0) 

**Root cause 2 — Deposits read the stored stale price:**

`getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()`, which returns the last-written storage value. No fresh computation is triggered before minting. [2](#0-1) 

`_beforeDeposit()` calls `getRsETHAmountToMint()` without first calling `updateRSETHPrice()`. [3](#0-2) 

**Root cause 3 — Price staleness is inherent to the design:**

`_updateRsETHPrice()` computes `totalETHInProtocol` from live on-chain balances (EigenLayer pod shares, NDC balances, etc.), but `rsETHPrice` is only written when `updateRSETHPrice()` is explicitly called. Between keeper invocations, rewards continuously accrue, making the stored price lower than the fair value `totalETHInProtocol / rsethSupply`. [4](#0-3) 

**Why the `pricePercentageLimit` check is insufficient:**

The check at lines 252–266 only reverts a non-manager caller if the price increase exceeds `pricePercentageLimit` relative to `highestRsethPrice`. Normal daily reward accrual (e.g., ~0.05% per day for a ~18% APR protocol) is far below any reasonable daily limit, so the attacker can freely call `updateRSETHPrice()` after depositing. Even if the limit were exceeded, the attacker does not need to call `updateRSETHPrice()` themselves — they only need to deposit at the stale price and wait for the keeper to update it, then sell on a secondary market. [5](#0-4) 

**Exploit flow:**

1. Rewards accrue in EigenLayer; `rsETHPrice` is stale (below fair value).
2. Attacker calls `depositETH()` or `depositAsset()` — minting is computed using the stale `rsETHPrice`, yielding excess rsETH.
3. Attacker (or keeper) calls `updateRSETHPrice()` — price advances to fair value.
4. Attacker sells rsETH on a secondary market (Curve, Uniswap) at the updated price, realizing a profit equal to the yield they extracted from existing holders.

## Impact Explanation

Existing rsETH holders suffer concrete dilution: the ETH backing per rsETH decreases because the attacker received rsETH at a below-fair-value price. The stolen value equals the fraction of accrued-but-unrecognized yield captured by the attacker's deposit. The attacker can exit immediately via secondary markets, bypassing the 8-day withdrawal delay. This is a direct, repeatable **theft of unclaimed yield** from honest rsETH holders.

**Impact: High** — Theft of unclaimed yield.

## Likelihood Explanation

- The price update is never called atomically inside `depositETH()` or `depositAsset()`, so any gap between keeper invocations is exploitable.
- Keeper failures, gas spikes, or deliberate attacker inaction (waiting for a larger gap) all increase the exploitable window.
- Normal daily reward accrual passes the `pricePercentageLimit` check freely, so the attacker can trigger the price update themselves after depositing.
- The attack is fully permissionless, requires no special role, and is repeatable every keeper cycle.

**Likelihood: Medium.**

## Recommendation

1. **Call `_updateRsETHPrice()` (or `updateRSETHPrice()`) at the start of `_beforeDeposit()`** so the mint calculation always uses a fresh price. This is the most robust fix.
2. **Alternatively**, restrict `updateRSETHPrice()` to a keeper/manager role and ensure the keeper is called atomically with deposits (e.g., via a multicall wrapper), preventing any gap between price update and deposit.
3. As a defense-in-depth measure, consider using a time-weighted or commit-reveal price to smooth out stale-price arbitrage opportunities.

## Proof of Concept

**Setup:**
- TVL = 1,100 ETH (100 ETH rewards accrued since last update)
- rsETH supply = 1,000
- Stored `rsETHPrice` = 1.00 ETH (stale); fair price = 1,100 / 1,000 = **1.10 ETH**

**Step 1 — Attacker calls `depositETH{value: 100 ether}(0, "")`:**

`getRsETHAmountToMint` computes: `(100e18 * 1e18) / 1.00e18 = 100 rsETH`

Fair amount: `100 / 1.10 ≈ 90.9 rsETH`. Attacker receives **9.1 excess rsETH**. [6](#0-5) 

**Step 2 — Attacker calls `LRTOracle.updateRSETHPrice()`:**

```
totalETHInProtocol = 1,200 ETH  (1,100 original + 100 deposited)
rsethSupply        = 1,100
newRsETHPrice      = 1,200 / 1,100 ≈ 1.0909 ETH
``` [1](#0-0) 

**Step 3 — Attacker sells 100 rsETH on a DEX at ≈ 1.0909 ETH:**

```
Proceeds = 100 × 1.0909 ≈ 109.09 ETH
Cost     = 100 ETH
Profit   ≈ 9.09 ETH
```

Existing holders (1,000 rsETH) now hold `1,000 × 1.0909 = 1,090.9 ETH` instead of the `1,100 ETH` they were entitled to — a loss of **≈ 9.1 ETH** transferred to the attacker.

**Foundry fork test plan:**

```solidity
function testStaleDepositYieldTheft() public fork {
    // 1. Simulate reward accrual: advance EigenLayer pod shares by 100 ETH
    //    without calling updateRSETHPrice()
    // 2. Record attacker ETH balance and rsETH supply
    // 3. Attacker calls depositETH{value: 100 ether}(0, "")
    // 4. Assert rsethMinted > getRsETHAmountToMint computed at fair price
    // 5. Attacker calls updateRSETHPrice()
    // 6. Assert rsETHPrice increased
    // 7. Assert attacker rsETH value (rsethMinted * newPrice) > 100 ETH
    // 8. Assert existing holder backing per rsETH decreased vs pre-attack
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-234)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
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

**File:** contracts/LRTDepositPool.sol (L665-665)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```
