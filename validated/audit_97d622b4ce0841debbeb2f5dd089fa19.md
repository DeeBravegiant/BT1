Audit Report

## Title
Stale `rsETHPrice` Denominator in `getRsETHAmountToMint()` Enables Excess rsETH Minting — (`contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` computes the mint amount using a live Chainlink asset price in the numerator but the cached `LRTOracle.rsETHPrice` state variable in the denominator. Because deposit functions never call `updateRSETHPrice()` before minting, any depositor can mint excess rsETH whenever the stored price lags the true current price. The gap is widest — and the public update path is blocked — when the price increase exceeds `pricePercentageLimit`, a condition that arises in normal protocol operation after staking rewards accrue.

## Finding Description
`LRTOracle` stores `rsETHPrice` as a plain state variable updated only inside `_updateRsETHPrice()`: [1](#0-0) [2](#0-1) 

The public entry point `updateRSETHPrice()` and the manager-gated `updateRSETHPriceAsManager()` are the only callers of `_updateRsETHPrice()`: [3](#0-2) 

`getRsETHAmountToMint()` divides a live oracle call by the stored state variable: [4](#0-3) 

`getAssetPrice()` always fetches fresh from the registered `IPriceFetcher` (Chainlink): [5](#0-4) 

Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before invoking `_beforeDeposit()` → `getRsETHAmountToMint()`: [6](#0-5) [7](#0-6) 

The price gate in `_updateRsETHPrice()` reverts for any non-manager caller when the true price has risen above `pricePercentageLimit` relative to `highestRsethPrice`: [8](#0-7) 

During this window, `rsETHPrice` is frozen at its old lower value. Any depositor can observe the on-chain `rsETHPrice` vs. the live computed price and deposit during the gap, receiving more rsETH shares than their contribution warrants.

## Impact Explanation
`rsETHPrice` increases monotonically as staking rewards accrue. A stale (lower) denominator inflates `rsethAmountToMint`:

```
rsethAmountToMint = (amount × freshAssetPrice) / staleRsETHPrice
                                                   ↑ too low → result too high
```

New depositors receive more rsETH shares than the ETH they contribute warrants. This dilutes existing rsETH holders' proportional claim on the underlying assets — the accrued yield that was reflected in the higher true price is partially transferred to the new depositor. This constitutes **theft of unclaimed yield** from existing holders.

**Impact: High** — matches the allowed scope "Theft of unclaimed yield."

## Likelihood Explanation
The staleness window exists continuously between price-update calls. It is especially wide and exploitable when `pricePercentageLimit` blocks the public `updateRSETHPrice()` path, which is a normal operating condition after a period of strong staking rewards. No special privilege is required; any unprivileged depositor can observe the on-chain `rsETHPrice` vs. the live computed price and deposit during the gap. The exploit is repeatable until a manager calls `updateRSETHPriceAsManager()`.

**Likelihood: Medium** — requires no special privilege; the condition arises in normal protocol operation.

## Recommendation
`getRsETHAmountToMint()` should compute the rsETH price fresh rather than reading the stored state variable. The safest fix is to expose a view-only price computation path that calls `_getTotalEthInProtocol()` inline (making `_getTotalEthInProtocol()` `internal view` instead of `private view`) and uses it as the denominator, so both numerator and denominator reflect current on-chain state. Alternatively, `depositETH()` and `depositAsset()` should call `updateRSETHPrice()` atomically before computing the mint amount, with appropriate handling for the manager-gated threshold case (e.g., using the last valid price when the threshold is exceeded and the protocol is not paused).

## Proof of Concept
1. At time T, `rsETHPrice = 1.05e18` (last updated). Staking rewards have since accrued; true price is `1.06e18`.
2. A user calls `updateRSETHPrice()`. The increase exceeds `pricePercentageLimit`, so the call reverts at: [9](#0-8) 
3. `rsETHPrice` remains `1.05e18` on-chain.
4. The attacker calls `depositAsset(stETH, 1e18, 0, "")`. Inside `getRsETHAmountToMint()`:
   - `getAssetPrice(stETH)` → `1.00e18` (fresh Chainlink)
   - `rsETHPrice()` → `1.05e18` (stale)
   - `rsethAmountToMint = 1e18 × 1e18 / 1.05e18 ≈ 0.952e18`
   - Correct amount at true price: `1e18 × 1e18 / 1.06e18 ≈ 0.943e18`
5. The attacker receives `≈ 0.009e18` excess rsETH per stETH deposited, extracted from existing holders' accrued yield.
6. **Foundry fork test plan**: Deploy against a mainnet fork; set `rsETHPrice` to a value below the live computed price (simulating staleness); call `depositAsset()` as an unprivileged address; assert that `rsethAmountToMint` exceeds the amount computed using the fresh price from `_getTotalEthInProtocol() / rsethSupply`; confirm the difference is non-zero and proportional to the staleness gap.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L86-88)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L110-112)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
