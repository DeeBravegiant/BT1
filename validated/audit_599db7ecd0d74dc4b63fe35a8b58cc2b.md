Audit Report

## Title
Single Asset Oracle Revert Permanently Blocks Fee Minting and Disables Downside-Protection Auto-Pause — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported asset inside a single loop with no error handling. If any one asset's price oracle permanently reverts, every call to `updateRSETHPrice()` reverts, freezing `rsETHPrice` at its last stored value. This permanently blocks protocol fee minting and prevents the downside-protection auto-pause from ever triggering.

## Finding Description

`_getTotalEthInProtocol()` iterates over all supported assets and calls `getAssetPrice(asset)` for each one:

```solidity
// LRTOracle.sol L336-348
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // ← no try/catch
    ...
}
```

`getAssetPrice` delegates to the registered `IPriceFetcher`:

```solidity
// LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

Each `IPriceFetcher` makes a live external call. `ChainlinkPriceOracle` calls `priceFeed.latestRoundData()` (ChainlinkPriceOracle.sol L52), and `RETHPriceOracle` calls `IrETH(rETHAddress).getExchangeRate()` (RETHPriceOracle.sol L39). Either can revert if the underlying feed or protocol is unavailable.

If any one of these external calls reverts, `_getTotalEthInProtocol()` reverts, which causes `_updateRsETHPrice()` to revert before it can:

1. **Update `rsETHPrice`** (LRTOracle.sol L313) — the stored price is frozen.
2. **Execute the downside-protection auto-pause** (LRTOracle.sol L270–282) — the block that pauses the deposit pool and withdrawal manager when price drops too far is never reached.
3. **Mint protocol fees** (LRTOracle.sol L299–311) — `_checkAndUpdateDailyFeeMintLimit` is never called.

Both `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()` (manager-only) call the same `_updateRsETHPrice()` internal function, so neither can bypass the revert.

## Impact Explanation

**Permanent freezing of unclaimed yield (Medium).** Protocol fee accrual is implemented entirely inside `_updateRsETHPrice()`. Every call to mint fees to the treasury is gated on a successful price update. If any supported asset's oracle permanently reverts, fee minting is permanently blocked for the entire protocol — not just for the failing asset. This matches the allowed impact "Permanent freezing of unclaimed yield."

The claim's Critical insolvency scenario additionally requires an operator to call `unlockQueue` with the stale price after a slashing event. Because `unlockQueue` is restricted to `onlyAssetTransferOrOperatorRole` (LRTWithdrawalManager.sol L280), that path is operator-dependent and does not constitute a permissionless exploit; it is not validated here.

## Likelihood Explanation

The protocol integrates with multiple external price feeds (Chainlink for stETH/ETHx, RocketPool's on-chain rate for rETH, etc.). With `n` supported assets there are `n` independent failure points. A Chainlink feed going stale/offline or an LST protocol pausing its exchange-rate function are realistic, non-attacker-controlled events. Any single failure is sufficient to trigger the condition. No privileged access or attacker action is required to cause the oracle failure itself.

## Recommendation

Wrap each `getAssetPrice(asset)` call inside `_getTotalEthInProtocol()` in a `try/catch` block. On revert, either skip the asset and emit a warning event, or revert only if a configurable minimum number of oracles fail. This is the standard pattern for multi-asset aggregators and prevents a single oracle failure from freezing the entire price-update and fee-minting mechanism.

## Proof of Concept

1. Protocol supports stETH, rETH, ETHx. All have deposits.
2. The Chainlink feed for rETH goes permanently offline — `latestRoundData()` reverts.
3. Anyone calls `updateRSETHPrice()`. Inside `_getTotalEthInProtocol()`, the loop reaches rETH and calls `getAssetPrice(rETH)` → `ChainlinkPriceOracle.getAssetPrice(rETH)` → `priceFeed.latestRoundData()` → **reverts**.
4. `updateRSETHPrice()` reverts. `rsETHPrice` is frozen. Fee minting is blocked.
5. Repeat step 3 at any future block — result is always the same revert.
6. Protocol fees that would have accrued from stETH and ETHx yield are permanently lost to the treasury.

Foundry test sketch:
```solidity
// Fork mainnet, mock rETH Chainlink feed to always revert
vm.mockCallRevert(rethFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector), "feed down");
// Advance time to accrue yield
vm.warp(block.timestamp + 1 days);
// Any caller
vm.expectRevert();
lrtOracle.updateRSETHPrice();
// Confirm fee minting state is unchanged
assertEq(lrtOracle.currentPeriodMintedFeeAmount(), 0);
```