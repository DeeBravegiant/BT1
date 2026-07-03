### Title
Unguarded External Oracle Calls in `_getTotalEthInProtocol()` Loop Permanently Brick `updateRSETHPrice()`, Freezing Protocol Fee Minting and Disabling the Price-Drop Circuit Breaker - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._getTotalEthInProtocol()` iterates over every supported asset and makes two unguarded external calls per iteration — one to a price oracle and one to `LRTDepositPool.getTotalAssetDeposits()`. If any single oracle reverts (e.g., Chainlink sequencer down, stale round, circuit breaker, or an asset-specific oracle bug), the entire `updateRSETHPrice()` call reverts. Because no try/catch is used, a single misbehaving oracle permanently bricks the rsETH price update mechanism, freezing protocol fee minting and disabling the automatic price-drop circuit breaker.

---

### Finding Description

`_getTotalEthInProtocol()` is a private function called unconditionally inside `_updateRsETHPrice()`, which is the sole implementation behind both `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()` (manager-only).

```
LRTOracle.sol lines 331-349:

function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
    address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
    address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
    uint256 supportedAssetCount = supportedAssets.length;

    for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
        address asset = supportedAssets[assetIdx];
        uint256 assetER = getAssetPrice(asset);                                    // <-- external oracle call
        uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr)
                                    .getTotalAssetDeposits(asset);                 // <-- external NDC calls
        totalETHInProtocol += totalAssetAmt.mulWad(assetER);
        unchecked { ++assetIdx; }
    }
}
```

`getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`. Concrete oracle implementations that can revert include:

- **`ChainlinkPriceOracle.getAssetPrice()`** — calls `priceFeed.latestRoundData()` with no staleness guard; Chainlink feeds can revert when the sequencer is down or the aggregator is deprecated.
- **`EthXPriceOracle.getAssetPrice()`** — explicitly reverts with `InvalidAsset()` if `asset != ethxAddress`; if Stader's config contract ever changes the ETHx token address, this oracle permanently reverts for the old address.
- **`RETHPriceOracle.getAssetPrice()`** — calls `IrETH(rETHAddress).getExchangeRate()`, which can revert if the Rocket Pool contract is paused or upgraded.

None of these calls are wrapped in a `try/catch`. A single revert in any iteration causes the entire transaction to revert.

---

### Impact Explanation

When `_getTotalEthInProtocol()` reverts:

1. **`updateRSETHPrice()` is permanently bricked** — the stored `rsETHPrice` state variable is never updated again until the oracle issue is resolved by admin action (which may require a governance upgrade).

2. **Protocol fee minting is permanently frozen** — `_updateRsETHPrice()` is the only code path that mints protocol fees (lines 300–308). With it bricked, all accrued yield that would have been minted as rsETH to the treasury is permanently lost. This is **High: permanent freezing of unclaimed yield**.

3. **The price-drop circuit breaker is disabled** — lines 270–281 of `_updateRsETHPrice()` auto-pause `LRTDepositPool` and `LRTWithdrawalManager` when the rsETH price drops beyond `pricePercentageLimit`. With `_updateRsETHPrice()` bricked, this safety mechanism cannot trigger, leaving the protocol exposed to undetected collateral devaluation.

4. **`rsETHPrice` becomes permanently stale** — deposits (`getRsETHAmountToMint`) and withdrawals (`getExpectedAssetAmount`) both read the stored `rsETHPrice`. A stale price means users deposit/withdraw at an incorrect exchange rate for an indefinite period.

---

### Likelihood Explanation

Realistic revert scenarios exist for every oracle in the loop:

- Chainlink's `latestRoundData()` is known to revert on L2s when the sequencer is offline, and on any chain when an aggregator is deprecated or a round is incomplete.
- `EthXPriceOracle` will revert with `InvalidAsset()` if Stader's config contract is upgraded and returns a new ETHx address — a routine protocol upgrade event.
- Any future asset added to the supported list brings its own oracle, any of which may have similar failure modes.

The protocol currently supports multiple LSTs (stETH, ETHx, rETH, sfrxETH), each with its own oracle. The probability that at least one oracle experiences a transient or permanent revert over the protocol's lifetime is high.

---

### Recommendation

Wrap each oracle call inside `_getTotalEthInProtocol()` in a `try/catch` block. If an oracle reverts, either skip that asset (with an emitted warning event) or revert with a descriptive error that identifies the failing asset, allowing operators to respond without bricking the entire price update:

```solidity
try IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset) returns (uint256 price) {
    assetER = price;
} catch {
    emit OracleCallFailed(asset);
    revert OracleReverted(asset); // or skip with continue
}
```

Alternatively, maintain a per-asset "last known good price" fallback so that a single oracle failure does not block the entire price update.

---

### Proof of Concept

1. Protocol has three supported assets: stETH, ETHx, rETH.
2. Stader upgrades their config contract, changing the ETHx token address.
3. `EthXPriceOracle.getAssetPrice(ethxOldAddress)` now reverts with `InvalidAsset()` because `ethxOldAddress != ethxAddress`.
4. Any call to `updateRSETHPrice()` now reverts at the `getAssetPrice(ethx)` call inside `_getTotalEthInProtocol()`.
5. `rsETHPrice` is frozen at its last value. Protocol fee minting stops. The price-drop circuit breaker is disabled.
6. Over time, accrued staking rewards are never reflected in `rsETHPrice`, and the treasury receives zero fee rsETH — permanent freezing of unclaimed yield.

Relevant code references: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/LRTOracle.sol (L298-316)
```text
        // mint protocol fee as rsETH if there's a fee to take
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

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
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

**File:** contracts/oracles/EthXPriceOracle.sol (L46-52)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != ethxAddress) {
            revert InvalidAsset();
        }

        return IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate();
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

**File:** contracts/oracles/RETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != rETHAddress) {
            revert InvalidAsset();
        }

        return IrETH(rETHAddress).getExchangeRate();
    }
```
