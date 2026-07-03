### Title
Chainlink Price Feed Return Values Not Validated in `ChainlinkPriceOracle`, Enabling Stale/Zero/Negative Prices to Corrupt rsETH Rate and Freeze the Protocol - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but performs no validation on the returned values. A zero, negative, or stale price from Chainlink propagates unchecked through `LRTOracle._getTotalEthInProtocol()` into `_updateRsETHPrice()`, corrupting the rsETH exchange rate and triggering an automatic protocol-wide pause, temporarily freezing all user deposits and withdrawals.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price with no sanitization:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Missing checks:
- **No `price > 0` guard**: Chainlink returns `0` during circuit-breaker events. Casting `int256(0)` to `uint256` silently returns `0`.
- **No staleness check** (`answeredInRound >= roundId`): A stale round is accepted as live.
- **No incomplete-round check** (`timestamp != 0`): An in-progress round is accepted.

The protocol's own `ChainlinkOracleForRSETHPoolCollateral` demonstrates the correct pattern — it enforces all three guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle registered for supported LST collateral assets (stETH, rETH, ETHx, sfrxETH) in `LRTOracle`. Its output flows directly into `_getTotalEthInProtocol()`:

```solidity
// contracts/LRTOracle.sol L339, L343
uint256 assetER = getAssetPrice(asset);   // calls ChainlinkPriceOracle
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`totalETHInProtocol` is then used to compute `newRsETHPrice`:

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [4](#0-3) 

---

### Impact Explanation

**Scenario A — Chainlink returns `price = 0` (circuit-breaker event):**

`getAssetPrice` returns `0` for the affected asset. `totalETHInProtocol` is underreported. `newRsETHPrice` collapses far below `highestRsethPrice`. The downside-protection branch fires:

```solidity
// contracts/LRTOracle.sol L270-281
if (newRsETHPrice < highestRsethPrice) {
    ...
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
``` [5](#0-4) 

`LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` are all paused. All user deposits and withdrawals are frozen until an admin manually unpauses — a **temporary freeze of funds**.

**Scenario B — Chainlink returns a negative `int256` price:**

`uint256(negative_int256)` wraps to a value near `type(uint256).max`. `totalETHInProtocol` is astronomically inflated. `newRsETHPrice` spikes, causing `getRsETHAmountToMint` to return near-zero rsETH for depositors:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

Depositors lose their LST collateral while receiving effectively zero rsETH — **temporary freezing of deposited funds / contract fails to deliver promised returns**.

**Scenario C — Stale price accepted silently:**

A stale price (e.g., from a Chainlink node outage) is used as the canonical rate. rsETH is minted or redeemed at an incorrect exchange rate, causing share/asset mis-accounting for all users interacting during the stale window.

---

### Likelihood Explanation

Chainlink price feeds have historically returned `0` or stale data during extreme market events and network congestion. The trigger (`updateRSETHPrice()`) is a **public, permissionless function** — any depositor or external keeper can call it at any time, including during a Chainlink disruption. The missing validation is a single-point failure covering all LST collateral assets simultaneously.

---

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();

return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, consider adding a `heartbeat`-based staleness threshold (e.g., `block.timestamp - updatedAt > MAX_DELAY`) per feed.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed enters a circuit-breaker state and returns `price = 0`.
2. Any address calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0`.
4. `totalETHInProtocol` is computed with stETH contributing `0` ETH value, severely underreporting TVL.
5. `newRsETHPrice` drops far below `highestRsethPrice`; `isPriceDecreaseOffLimit` evaluates to `true`.
6. `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are called.
7. All user deposits and withdrawals revert with `ContractPaused` until an admin manually unpauses — a temporary freeze of all user funds.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
