Audit Report

## Title
Single Asset Oracle Revert Permanently Blocks rsETH Price Updates and Fee Minting — (`contracts/LRTOracle.sol`)

## Summary
`LRTOracle._getTotalEthInProtocol()` iterates over every supported asset and calls `getAssetPrice(asset)` for each one inside a single loop with no error handling. If any one asset's price oracle reverts — for example, a Chainlink feed going offline — the entire `updateRSETHPrice()` call reverts, freezing `rsETHPrice` at its last stored value and permanently blocking protocol fee minting until an admin manually replaces the failing oracle. The downside-protection auto-pause mechanism also becomes inoperable for the duration of the failure.

## Finding Description
`_getTotalEthInProtocol()` (LRTOracle.sol, lines 336–348) iterates over all supported assets and calls `getAssetPrice(asset)` for each one:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // ← no try/catch
    ...
}
```

`getAssetPrice` (line 156–158) delegates to the registered `IPriceFetcher`, e.g. `ChainlinkPriceOracle.getAssetPrice()` (ChainlinkPriceOracle.sol, lines 49–55), which calls `priceFeed.latestRoundData()` — a live external call that can revert if the feed is offline, deprecated, or paused.

Because `_getTotalEthInProtocol()` is called unconditionally at line 231 of `_updateRsETHPrice()`, any revert from any single oracle propagates up through both `updateRSETHPrice()` (public, line 87) and `updateRSETHPriceAsManager()` (manager-only, line 94). Neither function has a try/catch wrapper.

The stored `rsETHPrice` (line 313) is only written at the very end of `_updateRsETHPrice()`, so it is never updated while the oracle is broken. The downside-protection block (lines 270–282) that auto-pauses the deposit pool and withdrawal manager when the price drops too far is also unreachable.

**Admin remediation is constrained**: `updatePriceOracleFor` (line 113) allows the admin to replace the oracle, but `removeSupportedAsset` in `LRTConfig.sol` (line 82) blocks removal of any asset whose total deposits exceed `maxNegligibleAmount`. In a live protocol with significant deposits, the failing asset cannot be removed — only replaced with a working oracle. Until a replacement is deployed and configured, the freeze persists with no on-chain automatic recovery.

## Impact Explanation
**Medium. Permanent freezing of unclaimed yield.**

Protocol fee minting occurs inside `_updateRsETHPrice()` at lines 299–311 via `_checkAndUpdateDailyFeeMintLimit`. While the oracle is broken, no fees are minted regardless of how much yield accrues. When the oracle is eventually fixed, the accumulated fee is calculated as `totalETHInProtocol − previousTVL` (line 244–246); if this amount exceeds `maxFeeMintAmountPerDay`, the call reverts again with `DailyFeeMintLimitExceeded`, requiring a second admin action to raise the daily cap before any fees can be minted. The yield that should have been minted during the frozen period is not recoverable without additional admin intervention, and the daily cap constraint can cause a portion of it to be permanently unclaimable.

The claimed Critical/insolvency path via `unlockQueue` is not validated: `unlockQueue` is restricted to `onlyAssetTransferOrOperatorRole` (line 280), and the function already provides caller-controlled price bounds via `_validatePrices` (lines 853–870) that an operator can use to reject a stale price. That path requires privileged operator action and is therefore out of scope per the rejection rules.

## Likelihood Explanation
The protocol integrates with multiple external Chainlink feeds and LST-native rate functions. A feed going stale, being deprecated, or an LST protocol pausing its exchange-rate function are all realistic, documented risks. With `n` supported assets there are `n` independent failure points. No special attacker capability is required — the condition is triggered by an external infrastructure event, not by any user action.

## Recommendation
Wrap each `getAssetPrice(asset)` call inside `_getTotalEthInProtocol()` in a `try/catch` block. On revert, either skip the asset and emit a warning event, or revert only if a configurable threshold of oracles fail. This is the standard pattern for multi-asset aggregators and prevents a single oracle failure from freezing the entire price-update and fee-minting mechanism.

## Proof of Concept
1. Deploy the protocol with three supported assets: stETH, rETH, ETHx, each with a Chainlink oracle.
2. Accumulate yield over time; `updateRSETHPrice()` succeeds and mints fees normally.
3. Simulate the rETH Chainlink feed reverting (e.g., mock `latestRoundData()` to always revert).
4. Call `updateRSETHPrice()`. Execution enters `_getTotalEthInProtocol()`, reaches the rETH iteration, calls `getAssetPrice(rETH)` → `ChainlinkPriceOracle.getAssetPrice(rETH)` → `priceFeed.latestRoundData()` → **reverts**. The entire call reverts.
5. Confirm `rsETHPrice` is unchanged. Confirm `feePeriodStartTime` and `currentPeriodMintedFeeAmount` are unchanged.
6. Advance time by several days. Call `updateRSETHPrice()` again — still reverts.
7. Fix the oracle. Call `updateRSETHPrice()`. The accumulated fee (days of yield) may now exceed `maxFeeMintAmountPerDay`, causing `DailyFeeMintLimitExceeded` to revert — demonstrating that a portion of the frozen yield is unclaimable without a second admin action to raise the cap. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L228-231)
```text
        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```
