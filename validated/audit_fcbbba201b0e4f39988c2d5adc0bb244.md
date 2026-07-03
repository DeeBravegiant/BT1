Audit Report

## Title
Static `ethValueInWithdrawal` Snapshot Causes Protocol Fee to Be Charged on stETH Rebase Yield — (`contracts/LRTConverter.sol`, `contracts/LRTOracle.sol`)

## Summary

When stETH is transferred to `LRTConverter` for Lido withdrawal, `ethValueInWithdrawal` is set to the ETH value of the stETH at that moment and never updated as stETH rebases. When `claimStEth` is called, the actual ETH received from Lido exceeds `ethValueInWithdrawal` by the full rebase delta. The next `updateRSETHPrice` call sees this delta as a TVL increase above `previousTVL` and mints protocol fee rsETH to treasury on it, charging rsETH holders a fee on yield that was already owed to them but was never reflected in the rsETH price.

## Finding Description

**Static snapshot at transfer time.**
`transferAssetFromDepositPool` records the ETH value of the stETH at the current oracle price: [1](#0-0) 
This integer is never recalculated. As stETH rebases during the Lido withdrawal period (up to 30+ days), `ethValueInWithdrawal` remains fixed at the snapshot value.

**TVL is understated throughout the withdrawal period.**
`getETHDistributionData` feeds `ethValueInWithdrawal` directly as the converter's ETH contribution: [2](#0-1) 
`_getTotalEthInProtocol` sums this via `getTotalAssetDeposits(ETH_TOKEN)`: [3](#0-2) 
Because `ethValueInWithdrawal` does not grow with the rebase, every `updateRSETHPrice` call during the withdrawal window computes a depressed `totalETHInProtocol`, so `previousTVL = rsethSupply × rsETHPrice` is also depressed by the full rebase delta.

**`claimStEth` delivers more ETH than `ethValueInWithdrawal` recorded.**
`claimStEth` claims ETH from Lido and forwards the full contract balance: [4](#0-3) 
Inside `_sendEthToDepositPool`, because actual ETH received (e.g. 1030) exceeds `ethValueInWithdrawal` (1000), the variable is zeroed and the full amount is forwarded: [5](#0-4) 
Net TVL change: `ethLyingInDepositPool` increases by 1030, `ethLyingInConverter` drops by 1000 → **+30 ETH** (the rebase delta).

**Protocol fee minted on the rebase delta.**
The next `_updateRsETHPrice` call computes `rewardAmount = totalETHInProtocol − previousTVL = 30 ETH` and charges the fee: [6](#0-5) 
This mints rsETH to treasury proportional to the rebase delta: [7](#0-6) 
The `pricePercentageLimit` guard can cause `updateRSETHPrice` to revert for non-manager callers on large jumps, but `updateRSETHPriceAsManager` bypasses it entirely: [8](#0-7) 

## Impact Explanation

**High — Theft of unclaimed yield.** The stETH rebase yield accrued during the withdrawal period is owed entirely to rsETH holders; it was never priced into rsETH (TVL was understated). When it arrives via `claimStEth`, the protocol treats the full rebase delta as new yield and mints `rebaseAmount × feeBPS / 10_000` worth of rsETH to treasury, directly diluting existing rsETH holders by that fee amount. The loss is concrete and quantifiable: for a 3% rebase on 1000 ETH at 10% fee, rsETH holders lose 3 ETH of yield to treasury.

## Likelihood Explanation

No special conditions are required beyond `protocolFeeInBPS > 0` (normal protocol configuration) and a non-zero stETH rebase during the withdrawal window. stETH rebases approximately every 24 hours; Lido withdrawal periods range from days to weeks, guaranteeing a non-trivial rebase delta on every withdrawal cycle. The operator calling `claimStEth` is routine protocol operation, not a malicious act. `updateRSETHPrice` is a public permissionless function callable by anyone immediately after `claimStEth`. The `maxFeeMintAmountPerDay` cap limits per-day damage but does not prevent the fee from being taken across multiple days or across multiple withdrawal requests. The vulnerability repeats on every stETH withdrawal cycle.

## Recommendation

Replace the static ETH snapshot in `ethValueInWithdrawal` with share-denominated accounting:

1. In `transferAssetFromDepositPool`, record the number of stETH **shares** (via `stETH.getSharesByPooledEth` or equivalent) rather than the ETH value.
2. In `getETHDistributionData` / `ethValueInWithdrawal`, compute the current ETH value of those shares using the live stETH exchange rate, so TVL tracks the rebase continuously throughout the withdrawal period.
3. In `_sendEthToDepositPool`, subtract the shares corresponding to the claimed request rather than comparing raw ETH amounts.

This ensures the rebase is reflected in TVL incrementally as it accrues, so `previousTVL` already incorporates the rebase by the time `claimStEth` is called, and no spurious TVL jump triggers an oversized fee.

## Proof of Concept

```solidity
// Foundry fork test outline (mainnet fork)
// Setup: stETH supported, protocolFeeInBPS = 1000 (10%)

// 1. Deposit 1000 stETH; record rsETHPrice_0, rsethSupply_0
// 2. operator calls transferAssetFromDepositPool(stETH, 1000e18)
//    → ethValueInWithdrawal = 1000e18
// 3. operator calls unstakeStEth(1000e18)
//    → Lido withdrawal NFT minted
// 4. vm.warp(block.timestamp + 30 days)
//    → stETH rebases ~3%; Lido finalizes withdrawal at 1030 ETH
// 5. Record treasury rsETH balance: bal_before
// 6. operator calls claimStEth(requestId, hint)
//    → 1030 ETH sent to deposit pool; ethValueInWithdrawal = 0
// 7. anyone calls updateRSETHPrice()
// 8. Record treasury rsETH balance: bal_after
// 9. fee_minted = bal_after - bal_before

// Assert: fee_minted == 0
// (30 ETH is a rebase correction, not new yield — no fee should be charged)

// Actual: fee_minted = (30e18 * 1000 / 10000) / newRsETHPrice > 0
// → FAIL: 3 ETH worth of rsETH minted to treasury, diluting rsETH holders
```

### Citations

**File:** contracts/LRTConverter.sol (L140-140)
```text
        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
```

**File:** contracts/LRTConverter.sol (L180-183)
```text
    function claimStEth(uint256 _requestId, uint256 _hint) external nonReentrant onlyLRTOperator {
        _claimStEth(_requestId, _hint);
        _sendEthToDepositPool(address(this).balance);
    }
```

**File:** contracts/LRTConverter.sol (L255-261)
```text
        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-307)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L331-343)
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
```
