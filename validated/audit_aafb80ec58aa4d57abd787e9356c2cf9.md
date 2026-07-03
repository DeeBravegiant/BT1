Audit Report

## Title
`LRTOracle._getTotalEthInProtocol` Lacks Fault Isolation — Single Oracle Revert Freezes rsETH Price Updates and Protocol Fee Minting - (File: contracts/LRTOracle.sol)

## Summary
`_getTotalEthInProtocol()` iterates over all supported assets and makes unguarded external calls to each asset's price oracle. If any single oracle reverts, the entire `updateRSETHPrice()` call reverts, freezing the rsETH price at its last stored value and permanently preventing protocol fee minting for the duration of the outage. No try-catch or low-level call wrapping exists anywhere in the call chain.

## Finding Description
`_getTotalEthInProtocol()` (lines 331–349) loops over every supported asset and calls `getAssetPrice(asset)` for each:

```solidity
// LRTOracle.sol L336-L343
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);   // unguarded external call
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

`getAssetPrice` (lines 156–158) delegates directly to a third-party oracle with no error handling:

```solidity
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
```

The four oracle adapters each make external calls to contracts outside the protocol's control:
- `ChainlinkPriceOracle` → `priceFeed.latestRoundData()` (line 52)
- `RETHPriceOracle` → `IrETH(rETHAddress).getExchangeRate()` (line 39)
- `SfrxETHPriceOracle` → `ISfrxETH(sfrxETHContractAddress).pricePerShare()` (line 40)
- `SwETHPriceOracle` → `ISwETH(swETHAddress).getRate()` (line 39)

`_getTotalEthInProtocol()` is called exclusively by `_updateRsETHPrice()` (line 231), which is called by the public `updateRSETHPrice()` (line 87–89) and manager-gated `updateRSETHPriceAsManager()` (line 94–96). A revert in any oracle propagates up through the entire call stack with no interception point. The `ChainlinkPriceOracle.getAssetPrice()` also performs an unchecked cast of `int256 price` to `uint256` (line 54), meaning a negative price answer from a deprecated or malfunctioning Chainlink feed causes an arithmetic revert in addition to any revert from the feed itself.

## Impact Explanation
When any oracle reverts, `_updateRsETHPrice()` cannot complete, so `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)` (line 306) is never reached. Protocol fees that accrued during the outage window are permanently lost — they are never minted and cannot be retroactively recovered once the oracle is fixed, because the fee calculation is based on the TVL delta at the time of the call. This matches **Medium: Permanent freezing of unclaimed yield**. Additionally, `rsETHPrice` remains stale, causing depositors to receive incorrect rsETH amounts, and the price-drop auto-pause circuit-breaker (lines 277–281) cannot fire.

## Likelihood Explanation
The protocol supports multiple LST assets each with an independent external oracle. Chainlink periodically deprecates price feeds; deprecated feeds are documented to revert on `latestRoundData()`. Rocket Pool, Frax, and Swell have each historically paused or upgraded their rate contracts. The probability that at least one of N independent oracles experiences a liveness failure increases with N. No attacker action is required — this is a passive failure mode triggered by normal external protocol lifecycle events. Any public caller invoking `updateRSETHPrice()` during such an event will observe the revert.

## Recommendation
Wrap each external oracle call inside `_getTotalEthInProtocol()` in a `try/catch` block. On failure, either skip the asset and emit a named event, or revert with a specific error identifying the broken oracle:

```solidity
try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 price) {
    totalETHInProtocol += totalAssetAmt.mulWad(price);
} catch {
    emit AssetPriceOracleFailed(asset);
    // protocol policy: skip asset or revert with named error
}
```

Additionally, `ChainlinkPriceOracle.getAssetPrice()` should validate that `price > 0` before casting to `uint256` to prevent silent arithmetic reverts from negative or zero answers.

## Proof of Concept

1. Deploy protocol with supported assets `[stETH, rETH, sfrxETH]`, each with its respective oracle adapter.
2. Simulate Rocket Pool upgrading its contracts such that the old `rETH.getExchangeRate()` reverts (e.g., `vm.mockCallRevert` in Foundry).
3. Call `LRTOracle.updateRSETHPrice()` from any EOA.
4. Execution path: `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(rETH)` → `RETHPriceOracle.getAssetPrice(rETH)` → `IrETH(rETHAddress).getExchangeRate()` → **reverts**.
5. The revert bubbles up; `rsETHPrice` is never updated; `mint(treasury, ...)` is never called.
6. Repeat step 3 on every subsequent block — the call continues to revert until an admin calls `updatePriceOracleFor(rETH, newOracle)`.
7. All protocol fees that should have accrued during the outage window are permanently lost.

**Foundry fork test sketch:**
```solidity
function test_oracleRevertFreezesFeeMinting() public {
    vm.mockCallRevert(rETHAddress, abi.encodeWithSelector(IrETH.getExchangeRate.selector), "paused");
    vm.expectRevert();
    lrtOracle.updateRSETHPrice();
    // rsETHPrice unchanged; no FeeMinted event emitted
}
```