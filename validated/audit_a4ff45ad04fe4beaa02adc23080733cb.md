Audit Report

## Title
`_checkAndUpdateDailyFeeMintLimit` Revert Permanently Stales `rsETHPrice`, Enabling Depositors to Mint rsETH at Below-Fair-Value Price — (File: `contracts/LRTOracle.sol`)

## Summary

When `maxFeeMintAmountPerDay` is set to a non-zero value and accumulated yield causes `rsethAmountToMintAsProtocolFee` to exceed that limit, `_checkAndUpdateDailyFeeMintLimit` reverts inside `_updateRsETHPrice()` before `rsETHPrice` is written. Because no privileged bypass exists — `updateRSETHPriceAsManager()` calls the identical internal function — the price is permanently frozen at its stale value until an admin raises the cap. New depositors can then call `depositAsset`/`depositETH` and receive more rsETH than the current fair value warrants, diluting existing holders of their accrued yield.

## Finding Description

**Exact code path:**

1. `updateRSETHPrice()` (public, `whenNotPaused`) calls `_updateRsETHPrice()`. [1](#0-0) 

2. Inside `_updateRsETHPrice()`, `previousTVL` is computed using the **stored** (potentially stale) `rsETHPrice`. [2](#0-1) 

3. When `totalETHInProtocol > previousTVL` and the protocol is not paused, `protocolFeeInETH` is derived from the accumulated reward. [3](#0-2) 

4. `rsethAmountToMintAsProtocolFee` is passed to `_checkAndUpdateDailyFeeMintLimit`. [4](#0-3) 

5. `_checkAndUpdateDailyFeeMintLimit` reverts unconditionally when the fee exceeds `maxFeeMintAmountPerDay`. [5](#0-4) 

6. The revert unwinds the entire call stack. The assignment `rsETHPrice = newRsETHPrice` at line 313 is **never reached**. [6](#0-5) 

7. `updateRSETHPriceAsManager()` calls the same `_updateRsETHPrice()` with no privileged bypass for the daily fee limit check — the manager hits the identical revert. [7](#0-6) 

**Why the condition is self-reinforcing:** Each subsequent call to `updateRSETHPrice()` recomputes `previousTVL` using the frozen stale `rsETHPrice`. As real TVL continues to grow (LST exchange rates accrue), the gap `totalETHInProtocol - previousTVL` widens, producing an even larger fee, making the revert permanent until an admin raises `maxFeeMintAmountPerDay`.

**How depositors exploit the stale price:** `getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`. With a stale (lower) price, the quotient is larger — depositors receive more rsETH per unit of asset than the true exchange rate warrants. [8](#0-7) 

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders have accrued yield embedded in the protocol's TVL. When `rsETHPrice` is frozen below fair value, new depositors are minted rsETH at the stale rate via `depositAsset`/`depositETH`, diluting the share of every existing holder. The yield that should have been reflected in a higher `rsETHPrice` is effectively transferred to new depositors. The protocol fee is also never collected for the blocked period.

## Likelihood Explanation

- `maxFeeMintAmountPerDay` is a live, settable parameter callable by any LRT manager (`setMaxFeeMintAmountPerDay`). A conservative value (e.g., 1 rsETH = 1e18) is a natural operational choice for a rate limiter. [9](#0-8) 
- Multi-day accumulation is routine: if `updateRSETHPrice()` is not called for several days (e.g., due to gas costs, keeper downtime, or the `pricePercentageLimit` guard blocking non-manager callers), the accumulated fee for a large TVL easily exceeds a per-day cap.
- No attacker action is required beyond depositing at the stale price. The freeze is triggered by normal protocol operation.

## Recommendation

Decouple fee minting from the price update. When the fee exceeds `maxFeeMintAmountPerDay`, cap the minted fee at the remaining daily limit (or skip fee minting entirely for that call) rather than reverting, and always write `rsETHPrice = newRsETHPrice`. For example:

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    uint256 mintable = _capAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    if (mintable > 0) {
        IRSETH(rsETHTokenAddress).mint(treasury, mintable);
        emit FeeMinted(treasury, mintable);
    }
}
rsETHPrice = newRsETHPrice; // always update price
```

This preserves the rate-limiting intent while ensuring `rsETHPrice` is always kept current.

## Proof of Concept

**Minimal call sequence (local fork):**

1. Deploy/fork at a block where the protocol has significant TVL.
2. As LRT manager, call `oracle.setMaxFeeMintAmountPerDay(1e18)` (1 rsETH cap).
3. Warp forward 3+ days to simulate multi-day LST rate accumulation (`vm.warp(block.timestamp + 3 days)`).
4. Call `oracle.updateRSETHPrice()` as an unprivileged caller — expect revert with `DailyFeeMintLimitExceeded`.
5. Call `oracle.updateRSETHPriceAsManager()` as the LRT manager — same revert, confirming no bypass.
6. Record `stalePrice = oracle.rsETHPrice()`.
7. As a new depositor, call `pool.depositAsset(rETH, 10 ether, 0, "")`.
8. Assert that rsETH minted exceeds `(10 ether * rETH.getExchangeRate()) / trueRsETHPrice` — confirming excess rsETH minted at the stale price.
9. Warp another day and repeat step 4 — confirm the revert persists (self-reinforcing), as `previousTVL` is still computed from the frozen `rsETHPrice`.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L132-135)
```text
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
    }
```

**File:** contracts/LRTOracle.sol (L205-207)
```text
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }
```

**File:** contracts/LRTOracle.sol (L233-234)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-303)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
