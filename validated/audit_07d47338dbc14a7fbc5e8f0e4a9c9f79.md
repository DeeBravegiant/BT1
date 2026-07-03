Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Dilutes Existing Holder Yield - (File: contracts/LRTDepositPool.sol / contracts/LRTOracle.sol)

## Summary
`LRTOracle.rsETHPrice` is a stored state variable updated only by explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`. The deposit path in `LRTDepositPool` reads this cached value directly via `getRsETHAmountToMint()` without triggering a price refresh. Between updates, accrued yield in EigenLayer strategies causes the real rsETH/ETH price to exceed the stored value, so new depositors receive excess rsETH, permanently diluting existing holders' yield.

## Finding Description
`LRTOracle.rsETHPrice` is declared as a plain storage variable at [1](#0-0)  and is written only inside `_updateRsETHPrice()`, which is invoked exclusively by `updateRSETHPrice()` (public, no-arg) and `updateRSETHPriceAsManager()` (manager-only). [2](#0-1) 

The deposit entry points `depositETH` and `depositAsset` both call `_beforeDeposit`, which calls `getRsETHAmountToMint` — a `view` function that reads the stored price directly: [3](#0-2) 

No call to `updateRSETHPrice()` or any live price computation occurs anywhere in the deposit path. [4](#0-3) 

The live price is computed inside `_updateRsETHPrice()` using `_getTotalEthInProtocol()`, which reads current EigenLayer strategy balances (via `INodeDelegator.getAssetBalance`) and live asset oracle prices. [5](#0-4)  This live computation is never triggered during deposits.

Between `updateRSETHPrice()` calls, yield accrues (stETH rebases increase strategy token balances; rETH/ETH oracle price rises). The real rsETH/ETH price increases above `rsETHPrice`. Because `rsethAmountToMint = amount * assetPrice / rsETHPrice`, a stale (lower) denominator causes new depositors to receive more rsETH than the current TVL justifies. When `updateRSETHPrice()` is next called, the new price is computed over the now-inflated rsETH supply, yielding a lower price than it would have been — existing holders' yield is permanently reduced.

## Impact Explanation
Every deposit made while `rsETHPrice` is stale mints excess rsETH. This excess dilutes the share of protocol TVL held by existing rsETH holders, reducing the yield they receive. The effect is systematic and continuous: yield accrues in EigenLayer strategies at all times, so `rsETHPrice` is always at least slightly stale between updates. This matches the allowed impact class: **Low — Contract fails to deliver promised returns, but doesn't lose value**. Principal is not at risk; only yield entitlement is reduced.

## Likelihood Explanation
Triggered by any unprivileged depositor calling `depositETH` or `depositAsset` at any time after yield has accrued since the last `updateRSETHPrice()` call. No special role, flash loan, or external condition is required. `updateRSETHPrice()` is public but there is no on-chain enforcement that it be called before a deposit. The effect is proportional to elapsed time since the last update and the yield rate of underlying assets (stETH ~3–5% APY, rETH similar), making it small per block but cumulative and repeatable across every deposit interval.

## Recommendation
In `getRsETHAmountToMint`, compute the current rsETH price on-the-fly using live TVL and rsETH total supply rather than reading the stored `rsETHPrice`. The read-only `_getTotalEthInProtocol()` logic already exists and can be extracted into an internal view function usable from `getRsETHAmountToMint` without state changes. Alternatively, require that `updateRSETHPrice()` is called atomically (e.g., in the same transaction) before any deposit that uses `rsETHPrice` for minting, enforced on-chain via a staleness timestamp check.

## Proof of Concept
1. Deploy a fork. Call `updateRSETHPrice()` at block N; record `rsETHPrice = P0`.
2. Advance several blocks (or warp time) so stETH rebases increase the strategy's token balance. The real rsETH/ETH price is now `P1 > P0`, but `rsETHPrice` still stores `P0`.
3. Alice calls `depositETH{value: 1 ether}(0, "")`. `getRsETHAmountToMint` computes `1e18 * 1e18 / P0`, minting more rsETH than `1e18 * 1e18 / P1` (the correct amount).
4. Record Alice's rsETH balance and the total rsETH supply before and after.
5. Call `updateRSETHPrice()` again. Observe that the new `rsETHPrice` is lower than it would have been without Alice's deposit, confirming dilution of pre-existing holders.
6. A Foundry fork test can assert: `rsETHPrice_after_deposit_and_update < expected_price_without_dilution`, where `expected_price_without_dilution` is computed as `totalETHInProtocol_before_deposit / rsETHSupply_before_deposit` (no new minting).

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

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
