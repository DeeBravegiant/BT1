Audit Report

## Title
TVL Inflation via Direct Token Donation Blocks `updateRSETHPrice()`, Causing Stale rsETH Price and Theft of Unclaimed Yield - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

## Summary

`LRTDepositPool` accepts unrestricted ETH via an open `receive()` function, and `LRTOracle._getTotalEthInProtocol()` computes TVL using raw `address.balance` and `IERC20.balanceOf()` reads. An unprivileged attacker can donate ETH or LST tokens to inflate `totalETHInProtocol`, causing `_updateRsETHPrice()` to revert with `PriceAboveDailyThreshold` (blocking all non-manager callers) or `DailyFeeMintLimitExceeded` (blocking all callers including the manager). While `rsETHPrice` remains stale, new depositors receive more rsETH than they should, diluting existing holders' unclaimed yield.

## Finding Description

**Root cause — open receive and raw balance reads:**

`LRTDepositPool` accepts arbitrary ETH at line 58: [1](#0-0) 

`getETHDistributionData()` reads raw balances at lines 480 and 496: [2](#0-1) 

`getAssetDistributionData()` reads raw ERC20 balances at lines 444 and 461: [3](#0-2) 

These feed into `_getTotalEthInProtocol()` via `getTotalAssetDeposits()`: [4](#0-3) 

**Revert path 1 — `PriceAboveDailyThreshold`:**

If the donation inflates `newRsETHPrice` beyond `pricePercentageLimit` relative to `highestRsethPrice`, any non-manager call to `updateRSETHPrice()` reverts: [5](#0-4) 

**Revert path 2 — `DailyFeeMintLimitExceeded`:**

The donation inflates `totalETHInProtocol > previousTVL`, increasing `protocolFeeInETH`. The resulting `rsethAmountToMintAsProtocolFee` is checked against `maxFeeMintAmountPerDay`. If it exceeds the limit, `_checkAndUpdateDailyFeeMintLimit` reverts — this path is hit by **both** `updateRSETHPrice()` and `updateRSETHPriceAsManager()`: [6](#0-5) [7](#0-6) 

**Stale price impact on new depositors:**

While `rsETHPrice` is stale (lower than actual), `getRsETHAmountToMint()` divides by the stale price, minting excess rsETH for new depositors: [8](#0-7) 

## Impact Explanation

**High — Theft of unclaimed yield.** Existing rsETH holders hold a claim on the protocol's TVL proportional to their rsETH share. When `rsETHPrice` is stale (lower than actual), new depositors receive more rsETH than their deposit warrants, diluting the existing holders' proportional claim on TVL. The donated ETH remains in the protocol and benefits all holders, but the over-minted rsETH for new depositors permanently reduces existing holders' share of that TVL — constituting theft of unclaimed yield from existing rsETH holders.

## Likelihood Explanation

- Any unprivileged external caller can trigger this via `address(lrtDepositPool).call{value: X}("")` or a direct ERC20 transfer to any tracked contract.
- The donation required to exceed `pricePercentageLimit` is proportional to TVL (e.g., ~1% of TVL if `pricePercentageLimit = 1e16`), making it costly but feasible for a large rsETH holder who partially recovers the cost through their own proportional share of the donated TVL.
- The attack is repeatable: each time the manager updates the price, the attacker can donate again.
- For the `DailyFeeMintLimitExceeded` path, the donation must be large enough to push the fee rsETH amount above `maxFeeMintAmountPerDay`, requiring a larger capital outlay.
- **Likelihood: Low-Medium** due to the permanent capital loss required.

## Recommendation

Replace raw `balanceOf` / `address.balance` reads with internal accounting variables that are only updated through controlled deposit, transfer, and withdrawal functions. Untracked donations would then not affect `totalETHInProtocol`. Alternatively, apply a TWAP or time-weighted smoothing to `totalETHInProtocol` before computing `newRsETHPrice` to prevent single-block donation spikes from triggering the threshold checks.

## Proof of Concept

1. Protocol state: TVL = 1000 ETH, rsETH supply = 950, `rsETHPrice` = 1.052 ETH, `highestRsethPrice` = 1.052 ETH, `pricePercentageLimit` = 1% (1e16).
2. Attacker executes: `(bool ok,) = address(lrtDepositPool).call{value: 11 ether}("");`
3. `totalETHInProtocol` = 1011 ETH; `newRsETHPrice` = 1011/950 ≈ 1.0642 ETH.
4. `priceDifference` = 0.0122 ETH > 1% × 1.052 = 0.01052 ETH → `isPriceIncreaseOffLimit = true`.
5. Any non-manager call to `updateRSETHPrice()` reverts with `PriceAboveDailyThreshold`; `rsETHPrice` stays at 1.052 ETH.
6. New depositor deposits 10 ETH → receives `10e18 / 1.052e18 ≈ 9.506` rsETH instead of the correct `10e18 / 1.0642e18 ≈ 9.397` rsETH — ~1.16% excess rsETH minted at the expense of existing holders.
7. For the `DailyFeeMintLimitExceeded` path: a larger donation inflates `protocolFeeInETH` such that `rsethAmountToMintAsProtocolFee > maxFeeMintAmountPerDay`, causing even `updateRSETHPriceAsManager()` to revert until the manager calls `setMaxFeeMintAmountPerDay()`.

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-461)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L480-496)
```text
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;
```

**File:** contracts/LRTDepositPool.sol (L516-520)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
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

**File:** contracts/LRTOracle.sol (L299-311)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
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
