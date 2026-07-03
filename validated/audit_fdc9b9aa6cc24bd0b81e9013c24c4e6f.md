### Title
Single Asset Oracle Revert Freezes `updateRSETHPrice()` and Blocks Deposits for Affected Asset — (`contracts/LRTOracle.sol`)

---

### Summary

`_getTotalEthInProtocol()` iterates over every supported asset and calls `getAssetPrice()` with no error handling. A revert from any one asset's price oracle (e.g., Chainlink `latestRoundData()` reverting on stale data or a circuit-breaker) propagates unconditionally through the entire call stack, causing `updateRSETHPrice()` to revert on every invocation and leaving `rsETHPrice` permanently stale until the oracle recovers.

---

### Finding Description

**Call chain — no try/catch at any level:**

```
updateRSETHPrice()                          LRTOracle.sol:87
  └─ _updateRsETHPrice()                    LRTOracle.sol:214
       └─ _getTotalEthInProtocol()          LRTOracle.sol:331
            └─ getAssetPrice(asset)         LRTOracle.sol:339
                 └─ IPriceFetcher(...).getAssetPrice(asset)   LRTOracle.sol:157
                      └─ priceFeed.latestRoundData()          ChainlinkPriceOracle.sol:52
```

`_getTotalEthInProtocol()` loops over all supported assets: [1](#0-0) 

For each asset it calls `getAssetPrice()`: [2](#0-1) 

Which delegates to the registered `IPriceFetcher`, e.g. `ChainlinkPriceOracle`: [3](#0-2) 

`latestRoundData()` is a well-known Chainlink call that reverts when the feed is stale, the sequencer is down, or a circuit-breaker fires. There is no `try/catch` anywhere in the chain, so the revert bubbles all the way up to `updateRSETHPrice()`.

**Secondary effect on deposits:**

`getRsETHAmountToMint()` also calls `lrtOracle.getAssetPrice(asset)` directly: [4](#0-3) 

If the oracle for the asset being deposited is the one reverting, every call to `depositAsset()` / `depositETH()` for that asset also reverts, because `_beforeDeposit()` calls `getRsETHAmountToMint()`: [5](#0-4) 

---

### Impact Explanation

| Effect | Scope |
|---|---|
| `updateRSETHPrice()` always reverts | All assets — entire price update mechanism frozen |
| `rsETHPrice` state variable becomes stale | Protocol-wide exchange rate incorrect |
| `depositAsset()` / `depositETH()` reverts | Only for the asset whose oracle is reverting |

The price update freeze is protocol-wide: even assets whose oracles are healthy cannot get their contribution reflected in `rsETHPrice` because the loop aborts on the first failing oracle. Deposits of the affected asset are blocked for the duration of the oracle outage. This satisfies **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

Chainlink feeds revert on stale answers (e.g., when `answeredInRound < roundId` checks are added by integrators, or when the feed itself is deprecated/paused). L2 sequencer downtime also causes Chainlink feeds to revert. The protocol supports multiple LST assets, each with its own Chainlink feed; any one of them experiencing a routine outage triggers this condition. This is a realistic, non-adversarial operational scenario.

---

### Recommendation

Wrap each per-asset oracle call in a `try/catch` inside `_getTotalEthInProtocol()`. On failure, either skip the asset (using its last known price as a fallback) or revert with a specific error that identifies the failing asset, allowing the operator to respond without the entire price update being blocked:

```solidity
try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 price) {
    totalETHInProtocol += totalAssetAmt.mulWad(price);
} catch {
    revert OracleFailedForAsset(asset);
}
```

Similarly, `getAssetPrice()` (used in `getRsETHAmountToMint()`) should propagate a clear error rather than a raw revert so callers can distinguish oracle failures from other errors.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

contract RevertingOracle {
    function getAssetPrice(address) external pure returns (uint256) {
        revert("stale");
    }
}

contract OracleFreezeTest is Test {
    // 1. Fork mainnet / testnet with LRTOracle deployed
    // 2. As LRTAdmin, call lrtOracle.updatePriceOracleFor(assetA, address(new RevertingOracle()))
    // 3. Assert updateRSETHPrice() reverts
    // 4. Assert lrtOracle.rsETHPrice() == stale value (unchanged)
    // 5. Assert depositPool.depositAsset(assetA, ...) reverts
    // 6. Assert depositPool.depositAsset(assetB, ...) succeeds but uses stale rsETHPrice

    function testOracleFreezesPriceUpdate() public {
        // setup: replace one asset oracle with RevertingOracle
        vm.prank(lrtAdmin);
        lrtOracle.updatePriceOracleFor(assetA, address(new RevertingOracle()));

        // price update is now permanently broken
        vm.expectRevert();
        lrtOracle.updateRSETHPrice();

        // rsETHPrice is stale
        assertEq(lrtOracle.rsETHPrice(), stalePriceSnapshot);

        // deposits of assetA are blocked
        vm.expectRevert();
        depositPool.depositAsset(assetA, 1 ether, 0, "");
    }
}
```

### Citations

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-665)
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
```
