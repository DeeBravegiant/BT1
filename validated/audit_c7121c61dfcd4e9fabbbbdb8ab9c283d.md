### Title
Single Oracle Failure in `_getTotalEthInProtocol` Loop Permanently Blocks rsETH Price Updates and Protocol Fee Minting - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle._getTotalEthInProtocol()` iterates over all supported assets and calls `getAssetPrice()` for each in a single unguarded loop. If any one oracle reverts — e.g., a Chainlink feed is deprecated, paused, or returns stale data that triggers a revert — the entire `updateRSETHPrice()` call reverts. This permanently blocks protocol fee minting until the broken oracle is fixed or removed, freezing unclaimed yield. It also disables the downside auto-pause protection.

### Finding Description
In `LRTOracle._getTotalEthInProtocol()`, the function loops over all supported assets and calls `getAssetPrice(asset)` for each:

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);          // ← can revert
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    unchecked { ++assetIdx; }
}
```

`getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`. Chainlink's `latestRoundData()` is documented to revert when a feed is deprecated or the aggregator is paused. Other adapters (`EthXPriceOracle`, `RETHPriceOracle`, `SfrxETHPriceOracle`, `SwETHPriceOracle`) may also revert under failure conditions.

If any single oracle reverts, `_getTotalEthInProtocol()` reverts, which propagates through `_updateRsETHPrice()` to the public `updateRSETHPrice()`. Since `_updateRsETHPrice()` is the **only** place protocol fees are minted as rsETH to the treasury, a single broken oracle permanently blocks fee collection. The downside auto-pause mechanism (which pauses the protocol if the price drops beyond `pricePercentageLimit`) is also blocked, leaving the protocol unable to self-protect during a price crash. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
**Medium: Permanent freezing of unclaimed yield.** Protocol fees (minted as rsETH to the treasury) cannot be collected for the entire duration that any supported asset's oracle is broken. The longer the oracle remains broken, the more yield is permanently lost. Secondarily, the auto-pause mechanism is disabled, which could allow users to withdraw at a stale (potentially favorable) price, causing losses to remaining rsETH holders.

### Likelihood Explanation
**Medium.** The protocol supports multiple LST assets (stETH, ETHx, rETH, sfrxETH, swETH), each with its own price oracle. Chainlink feeds have historically reverted in production (deprecated aggregators, sequencer downtime on L2 deployments, circuit breakers). The more assets are supported, the higher the cumulative probability that at least one oracle fails at any given time. No special attacker action is required — the failure is triggered by normal oracle lifecycle events.

### Recommendation
Wrap each oracle call in a `try/catch` block inside the loop. If an oracle reverts, either skip that asset (using its last known price or zero contribution) or emit an event and continue. This mirrors the recommendation in the reference report: contain failures so that a single broken component does not block the entire operation.

```solidity
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 assetER) {
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
        totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    } catch {
        emit OracleFetchFailed(asset);
        // optionally revert or use last known price
    }
    unchecked { ++assetIdx; }
}
```

### Proof of Concept
1. Protocol supports five assets: ETH, stETH, ETHx, rETH, sfrxETH.
2. The Chainlink aggregator backing `EthXPriceOracle` is deprecated; `latestRoundData()` reverts.
3. Any caller (including an unprivileged user or a keeper bot) calls `updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` iterates assets; when it reaches ETHx, `getAssetPrice(ETHx)` reverts.
5. The entire `updateRSETHPrice()` call reverts.
6. Protocol fees cannot be minted; the treasury receives no yield.
7. The auto-pause mechanism cannot trigger even if rsETH price drops sharply.
8. This state persists until the admin calls `updatePriceOracleFor` to replace or remove the broken oracle — during which all accrued yield is permanently lost. [5](#0-4) [6](#0-5)

### Citations

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

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

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

**File:** contracts/LRTOracle.sol (L299-312)
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
