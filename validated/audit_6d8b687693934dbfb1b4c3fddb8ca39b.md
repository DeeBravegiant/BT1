Audit Report

## Title
Stale `rsETHPrice` vs. Live `getAssetPrice` Mismatch in Deposit Minting Allows Over-Minting of rsETH at Existing Holders' Expense — (`contracts/LRTDepositPool.sol`)

## Summary

`LRTDepositPool.getRsETHAmountToMint` divides a live, per-transaction asset price by a stored, lazily-updated `rsETHPrice` state variable. Because `rsETHPrice` is only written inside `_updateRsETHPrice()` — which is never called atomically with a deposit — any period of rising LST prices creates a persistent window where depositors receive more rsETH than their deposit is worth at the true current exchange rate, directly diluting the accrued yield of all existing rsETH holders.

## Finding Description

**Root cause — asymmetric price freshness in the minting formula:**

`LRTDepositPool.getRsETHAmountToMint` (line 520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

- **Numerator** — `lrtOracle.getAssetPrice(asset)` is a live, view-only call that delegates to an external `IPriceFetcher` and returns the current market price at the moment of the transaction. [2](#0-1) 

- **Denominator** — `lrtOracle.rsETHPrice()` reads the public state variable `rsETHPrice`, which is only written at the end of `_updateRsETHPrice()`. [3](#0-2) [4](#0-3) 

**No atomic update on deposit:**

Neither `depositETH` nor `depositAsset` calls `updateRSETHPrice()` before computing the mint amount. `_beforeDeposit` calls `getRsETHAmountToMint` directly without refreshing the stored price. [5](#0-4) [6](#0-5) 

`updateRSETHPrice()` is a separate, permissionless public function that must be called explicitly: [7](#0-6) 

**The mismatch:**

The true rsETH price is `totalETHInProtocol / rsethSupply`, where `totalETHInProtocol` is itself computed using live `getAssetPrice()` calls for every supported asset. When any supported asset's price rises after the last oracle update:

- Stored `rsETHPrice` is **lower** than the true current price (computed with old, lower asset prices).
- Live `getAssetPrice(asset)` in the numerator is **higher** (current market).

The minting formula therefore becomes:
```
rsethAmountToMint = (amount × freshHigherAssetPrice) / staleOldLowerRsETHPrice
                  > (amount × freshHigherAssetPrice) / trueCurrentRsETHPrice
```

The depositor receives more rsETH than their deposit is worth at the true current exchange rate. The excess rsETH represents a claim on yield that was already accrued by existing holders.

**Existing guards are insufficient:**

- `minRSETHAmountExpected` is a depositor-side slippage guard; it protects the depositor, not existing holders.
- `pricePercentageLimit` in `_updateRsETHPrice` limits per-update price jumps but does not prevent the stale-price window between updates.
- `updateRSETHPrice()` being permissionless does not help because there is no on-chain enforcement that it is called before a deposit.

## Impact Explanation

**High — Theft of unclaimed yield from existing rsETH holders.**

Every rsETH minted in excess of the true exchange rate dilutes the proportional claim of all existing holders on the protocol's TVL. The value extracted is the yield accrued by the underlying LSTs (stETH, ETHx, etc.) since the last oracle update — yield that belongs to existing holders but has not yet been crystallized into a higher `rsETHPrice`. This matches the allowed impact class: **Theft of unclaimed yield**.

## Likelihood Explanation

**Medium-High.** No special privileges, flash loans, or governance access are required. Any ordinary depositor calling `depositAsset()` or `depositETH()` benefits from the mismatch whenever asset prices have risen since the last oracle update. LST prices (stETH, ETHx) accrue value continuously with each Ethereum epoch, so the stale-price window is always open to some degree between oracle updates. The exploit is passively available to every depositor and can be repeated indefinitely. [8](#0-7) 

## Recommendation

Call `lrtOracle.updateRSETHPrice()` at the beginning of `depositAsset()` and `depositETH()` (before `_beforeDeposit` is invoked), so that both the asset price and the rsETH price are computed from the same on-chain state. Alternatively, replace the stored `rsETHPrice` read in `getRsETHAmountToMint` with a live, view-only computation: `_getTotalEthInProtocol() / rsethSupply`, ensuring both sides of the division reflect the same block's prices.

## Proof of Concept

**Setup:**
- Protocol holds 100 stETH (each worth 1.00 ETH) and 100 ETHx (each worth 1.00 ETH).
- Total TVL = 200 ETH, rsETH supply = 200, stored `rsETHPrice` = 1.00 ETH.

**Price movement (no oracle update):**
- stETH appreciates to 1.05 ETH per token (normal LST yield accrual over several days).
- True rsETH price = (100 × 1.05 + 100 × 1.00) / 200 = 205 / 200 = **1.025 ETH**.
- Stored `rsETHPrice` = **1.00 ETH** (stale).

**Attacker deposits 10 stETH without calling `updateRSETHPrice()` first:**
```
rsethAmountToMint = (10 × 1.05e18) / 1.00e18 = 10.5 rsETH   ← actual minted
true entitlement  = (10 × 1.05e18) / 1.025e18 ≈ 10.244 rsETH
over-minted       ≈ 0.256 rsETH  (≈ 2.5% excess)
```

The 0.256 rsETH excess represents ~0.262 ETH of value extracted from existing holders. At scale (e.g., a 1,000 stETH deposit), the over-minting is ~25.6 rsETH ≈ 26.2 ETH of dilution imposed on all current rsETH holders.

**Foundry fork test plan:**
1. Fork mainnet at a block where stETH/ETHx prices have risen since the last `updateRSETHPrice` call.
2. Record `rsETHPrice` (stored) and compute the true price via `_getTotalEthInProtocol() / rsethSupply`.
3. Call `depositAsset(stETH, 1000e18, 0, "")` without calling `updateRSETHPrice` first.
4. Assert that the rsETH minted exceeds `1000e18 * getAssetPrice(stETH) / trueCurrentRsETHPrice`.
5. Confirm the difference is non-zero and proportional to the price staleness.

### Citations

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
