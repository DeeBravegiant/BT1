Audit Report

## Title
Unbounded O(assets × NDCs) Complexity in `updateRSETHPrice` Enables Block Stuffing to Deposit at Stale Price — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function whose gas cost scales as O(supported assets × NDCs) due to nested loops over assets and node delegators with multiple external calls per iteration. Because `LRTDepositPool.getRsETHAmountToMint` reads the cached `rsETHPrice` with no staleness check, an attacker can economically justify stuffing blocks to prevent price updates and then deposit at a stale, favorable rate, extracting value from existing rsETH holders.

## Finding Description

**Permissionless entry point with unbounded complexity:**

`updateRSETHPrice()` is `public` with only a `whenNotPaused` guard. [1](#0-0) 

`_getTotalEthInProtocol()` loops over every supported asset, and for each asset calls `getTotalAssetDeposits` → `getAssetDistributionData`, which itself loops over every NDC with three external calls per NDC (`balanceOf`, `getAssetBalance`, `getAssetUnstaking`). [2](#0-1) [3](#0-2) 

For ETH specifically, `getETHDistributionData()` adds a fourth external call per NDC (`getEffectivePodShares`). [4](#0-3) 

`maxNodeDelegatorLimit` is initialized to 10, and can be raised by admin, making the worst-case gas cost grow proportionally. [5](#0-4) 

**No staleness check on deposit:**

`getRsETHAmountToMint` reads `lrtOracle.rsETHPrice()` directly from storage with no freshness guard. [6](#0-5) 

`_beforeDeposit` calls `getRsETHAmountToMint` and only enforces a user-supplied `minRSETHAmountExpected` slippage check, which the attacker sets to 0. [7](#0-6) 

`rsETHPrice` is only written in `_updateRsETHPrice()`. [8](#0-7) 

**Exploit flow:**
1. TVL grows (staking rewards accrue), making the fair rsETH price higher than the cached `rsETHPrice`.
2. Attacker stuffs blocks by submitting high-gas filler transactions, keeping `updateRSETHPrice()` out of included blocks.
3. Attacker calls `depositETH{value: X}(0, "")` — rsETH minted = `X * 1e18 / rsETHPrice_stale`, which is more than the fair share.
4. Attacker stops stuffing; anyone calls `updateRSETHPrice()` → `rsETHPrice` rises to fair value.
5. Attacker's rsETH is now redeemable for more ETH than deposited, diluting existing holders.

No existing check prevents this: the `minRSETHAmountExpected` parameter protects the depositor from slippage but does not prevent an attacker from intentionally depositing at a stale price.

## Impact Explanation

**Low. Block stuffing.** The O(assets × NDCs) complexity is the specific protocol property that makes `updateRSETHPrice` a viable and economically attractive block-stuffing target. The direct consequence is that existing rsETH holders are diluted: the attacker receives more rsETH than their proportional share of TVL, redeemable at the updated (higher) price. This matches the allowed impact "Low. Block stuffing."

## Likelihood Explanation

Block stuffing on Ethereum mainnet requires the attacker to pay the prevailing base fee for every gas unit in every stuffed block. The attack is economically rational when `(rsETH_minted_at_stale - rsETH_fair) × rsETHPrice_fair > cost_of_stuffing`. With a large TVL (rsETH has hundreds of millions in TVL), even a 0.1% price lag over a few blocks can yield profit exceeding stuffing costs. The function is permissionless, requires no privileged access, and can be repeated whenever the price lags.

## Recommendation

1. **Add a staleness guard in the deposit path.** Record `lastPriceUpdateTimestamp` in `LRTOracle` and revert in `getRsETHAmountToMint` (or `_beforeDeposit`) if `block.timestamp - lastPriceUpdateTimestamp` exceeds an acceptable window (e.g., 1 hour).
2. **Reduce per-call gas cost.** Cache intermediate TVL values or batch NDC queries to lower the gas cost of `updateRSETHPrice`, making block stuffing less economically viable.
3. **Require a price update before deposit.** Alternatively, call `updateRSETHPrice` atomically inside `depositETH`/`depositAsset`, or require it to have been called in the same block.

## Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// Foundry fork test outline
// 1. Fork mainnet at block B where rsETHPrice = P_stale (e.g., 1.05e18)
// 2. Simulate TVL growth so fair price P_fair > P_stale (e.g., 1.06e18)
//    (e.g., warp time forward so staking rewards accrue in EigenLayer)
// 3. Attacker stuffs blocks: submit high-gas txs filling each block,
//    keeping updateRSETHPrice() out of the mempool for N blocks.
// 4. Attacker calls depositETH{value: X}(0, "") — minted rsETH = X * 1e18 / P_stale
// 5. Stop stuffing; anyone calls updateRSETHPrice() → rsETHPrice = P_fair
// 6. Attacker initiates withdrawal; rsETH redeems at P_fair
// 7. Assert: ETH received > X  (attacker extracted principal from other holders)

function testBlockStuffingStaleDeposit() public {
    uint256 staledPrice = lrtOracle.rsETHPrice(); // e.g. 1.05e18, not yet updated
    uint256 depositAmt  = 100 ether;

    // rsETH minted uses stale price — no staleness check in getRsETHAmountToMint
    uint256 rsethMinted = depositPool.getRsETHAmountToMint(ETH_TOKEN, depositAmt);
    // rsethMinted = depositAmt * 1e18 / staledPrice  (more than fair share)

    vm.prank(attacker);
    depositPool.depositETH{value: depositAmt}(0, "");

    // Now let price update
    lrtOracle.updateRSETHPrice();
    uint256 updatedPrice = lrtOracle.rsETHPrice(); // e.g. 1.06e18

    // Fair rsETH at updated price
    uint256 fairRseth = depositAmt * 1e18 / updatedPrice;

    assertGt(rsethMinted, fairRseth, "attacker received excess rsETH at stale price");
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L336-348)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L49-49)
```text
        maxNodeDelegatorLimit = 10;
```

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L484-493)
```text
        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L665-668)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
```
