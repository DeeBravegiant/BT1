Audit Report

## Title
Stale ETH Value Snapshot in `LRTConverter.ethValueInWithdrawal` Understates Protocol TVL, Enabling Theft of Unclaimed Yield — (File: `contracts/LRTConverter.sol`)

## Summary
`LRTConverter.transferAssetFromDepositPool` snapshots the ETH value of transferred LST assets (e.g., stETH) at the moment of transfer and stores it in `ethValueInWithdrawal`. This value is never refreshed during the withdrawal period. Because stETH is a rebasing token whose balance grows over time, the actual ETH value of converter assets continuously exceeds the stale snapshot, causing `_getTotalEthInProtocol()` to understate the protocol's TVL and deflate `rsETHPrice`. New depositors mint rsETH at this deflated price, permanently diluting existing holders' unclaimed yield.

## Finding Description

**Root cause — stale snapshot at transfer time:**

`LRTConverter.transferAssetFromDepositPool` (L140) records the ETH value of transferred assets using the oracle price at the moment of transfer:

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
``` [1](#0-0) 

This value is never updated to reflect subsequent stETH rebasing or price appreciation. It is only decremented in `_sendEthToDepositPool` (L255–259), which is called only after the Lido withdrawal completes (days later):

```solidity
if (ethValueInWithdrawal > _amount) {
    ethValueInWithdrawal -= _amount;
} else {
    ethValueInWithdrawal = 0;
}
``` [2](#0-1) 

**Exclusive accounting path for converter assets:**

For non-ETH assets, `getAssetDistributionData` explicitly zeroes out the converter's contribution:

```solidity
assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
``` [3](#0-2) 

The stETH in the converter is therefore **exclusively** accounted for via `ethValueInWithdrawal`, read in `getETHDistributionData()`:

```solidity
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [4](#0-3) 

**Price computation uses stale TVL:**

`_getTotalEthInProtocol()` aggregates this stale value alongside live prices for all other asset locations: [5](#0-4) 

The rsETH price is then computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [6](#0-5) 

With `totalETHInProtocol` understated, `newRsETHPrice` is lower than the true value.

**Exploit path:**

New depositors call `depositAsset` or `depositETH`, which calls `getRsETHAmountToMint`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [7](#0-6) 

With a deflated `rsETHPrice`, `rsethAmountToMint` is inflated — new depositors receive more rsETH than they are entitled to. When `claimStEth` eventually calls `_sendEthToDepositPool` with the actual (larger) ETH amount, `ethValueInWithdrawal` is set to 0 and the excess ETH enters the pool, but the rsETH supply has already been permanently inflated. The dilution cannot be reversed.

**Why existing guards are insufficient:**

The `pricePercentageLimit` check in `_updateRsETHPrice` guards against large single-step price jumps, but the understatement here is gradual (~0.11% over 10 days for stETH at 4% APY), well within any reasonable daily threshold. `updateRSETHPrice()` is a public, permissionless function callable by anyone, including a depositor seeking to exploit the deflated price.

## Impact Explanation

**High — Theft of unclaimed yield.**

The rebasing rewards accrued by stETH while held in the converter belong to existing rsETH holders. Because `ethValueInWithdrawal` does not reflect these rewards, new depositors can mint rsETH at a deflated price and permanently capture a portion of that yield. The dilution is irreversible: once rsETH is minted at the deflated price and the withdrawal completes, the extra ETH is absorbed into the pool but the inflated rsETH supply remains. This matches the allowed impact class "High — Theft of unclaimed yield."

## Likelihood Explanation

`transferAssetFromDepositPool` is a normal protocol operation (restricted to `ASSET_TRANSFER_ROLE`) performed whenever stETH needs to be unstaked via Lido. No malicious operator action is required — the stale accounting window opens automatically as part of routine operations. The Lido withdrawal queue routinely takes 1–14 days. During this entire window, `updateRSETHPrice()` can be called by any unprivileged external user (including a depositor), and deposits can be made at the deflated price. The conditions are regularly met in normal protocol operation.

## Recommendation

Replace the fixed ETH value snapshot with tracking of the raw asset amount held in the converter. Expose a view function from `LRTConverter` that computes the current ETH value of held assets using live oracle prices (i.e., `IERC20(stETH).balanceOf(address(this)) * lrtOracle.getAssetPrice(stETH) / 1e18`), and have `getETHDistributionData()` call that view function instead of reading the stale `ethValueInWithdrawal`. Alternatively, update `ethValueInWithdrawal` on every `updateRSETHPrice()` call by re-evaluating converter holdings at current prices. The `ethValueInWithdrawal` variable can be retained for the `_sendEthToDepositPool` accounting adjustment, but the TVL computation should always use live prices.

## Proof of Concept

1. Operator calls `transferAssetFromDepositPool(stETH, 10_000e18)` when stETH price = `1.05e18`.
   - `ethValueInWithdrawal = 10_000 * 1.05 = 10_500 ETH`
   - Converter holds 10,000 stETH.

2. Operator calls `unstakeStEth(10_011e18)` after 10 days of rebasing (stETH balance grew to ~10,011 due to rebasing). Lido issues a withdrawal NFT for ~10,522 ETH.
   - `ethValueInWithdrawal` remains `10_500 ETH` (stale).

3. Anyone calls `updateRSETHPrice()`. `_getTotalEthInProtocol()` uses `ethValueInWithdrawal = 10_500` instead of the true `~10,522`, understating TVL by ~22 ETH. `rsETHPrice` is computed lower than the true value.

4. A new depositor calls `depositETH` and receives more rsETH than they are entitled to at the expense of existing holders (rsETH minted using the deflated price denominator).

5. Operator calls `claimStEth`. `_sendEthToDepositPool(10_522 ETH)` is invoked. Since `10_522 > 10_500`, `ethValueInWithdrawal` is set to 0. The 22 ETH of yield is now in the pool, but the rsETH supply is permanently inflated — the dilution is irreversible.

**Foundry fork test outline:**
- Fork mainnet; deploy/configure protocol with stETH as supported asset.
- Call `transferAssetFromDepositPool(stETH, 10_000e18)` and record `rsETHPrice` and `ethValueInWithdrawal`.
- `vm.warp(block.timestamp + 10 days)` to simulate rebasing (or use `deal` to set stETH balance to rebased amount).
- Call `updateRSETHPrice()` and assert `rsETHPrice` is lower than the true value (computed using live stETH balance × current price).
- Have a new depositor call `depositETH` and record rsETH minted.
- Call `claimStEth`; verify the ETH received exceeds `ethValueInWithdrawal` and that existing holders' rsETH share is diluted.

### Citations

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTConverter.sol (L255-259)
```text
        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
```

**File:** contracts/LRTDepositPool.sol (L460-460)
```text
        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
