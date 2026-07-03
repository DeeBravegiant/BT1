### Title
Missing Non-Positive Price Validation Before `uint256` Cast in `ChainlinkPriceOracle.getAssetPrice` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice` retrieves a Chainlink `int256 price` and casts it directly to `uint256` without validating that `price > 0`. A zero or negative value from the feed is silently mishandled: zero produces a zero price that collapses the protocol's TVL accounting and triggers an erroneous downside-protection pause; a negative value wraps to a near-`type(uint256).max` figure that massively inflates TVL and rsETH minting. The same codebase already applies the correct guard (`if (ethPrice <= 0) revert InvalidPrice()`) in `ChainlinkOracleForRSETHPoolCollateral.getRate`, confirming the team is aware of the pattern but omitted it from the core oracle.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` (the `IPriceFetcher` implementation wired into `LRTOracle` for every supported LST) performs:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no guard on `price`. Chainlink's `AggregatorV3Interface` returns `int256` precisely because the value can be zero (circuit-breaker / uninitialized round) or, in degenerate aggregator states, negative. The sibling contract in the same repository already handles this correctly:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle registered for supported LSTs (stETH, ETHx, rETH, sfrxETH, swETH) via `LRTOracle.assetPriceOracle`. Its output feeds two critical paths:

1. **`LRTOracle._getTotalEthInProtocol`** — sums `totalAssetAmt.mulWad(assetER)` for every supported asset to derive `totalETHInProtocol`, which is then used to compute and store `rsETHPrice`. [3](#0-2) 

2. **`LRTDepositPool.getRsETHAmountToMint`** — computes `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` to determine how many rsETH tokens to mint per deposit. [4](#0-3) 

---

### Impact Explanation

**Case A — `price == 0` (zero price, e.g., Chainlink circuit-breaker or uninitialized round):**

- `getAssetPrice` returns `0`.
- `_getTotalEthInProtocol` drops the entire TVL contribution of the affected asset to zero.
- `newRsETHPrice` falls below `highestRsethPrice`; if the drop exceeds `pricePercentageLimit`, the downside-protection branch executes: `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` are all paused. [5](#0-4) 
- All user deposits and withdrawals are frozen until an admin manually unpauses — **temporary freezing of funds (Medium)**.
- Even without hitting the threshold, `getRsETHAmountToMint` returns `0`, so any depositor calling `depositAsset` with `minRSETHAmountExpected == 0` loses their tokens while receiving nothing.

**Case B — `price < 0` (negative price, degenerate aggregator state):**

- `uint256(price)` wraps to approximately `2^256 − |price|`, an astronomically large number.
- `_getTotalEthInProtocol` returns a near-`type(uint256).max` value; `newRsETHPrice` overflows or is capped only by the `pricePercentageLimit` revert — but `updateRSETHPrice` is callable by anyone, so an attacker can time the call to a moment when the limit is unset or large.
- `getRsETHAmountToMint` returns an enormous rsETH amount for a trivial deposit, enabling direct theft of protocol funds — **protocol insolvency (Critical)**.

---

### Likelihood Explanation

Chainlink LST/ETH feeds (stETH/ETH, ETHx/ETH, rETH/ETH) have returned `0` during aggregator initialization, failed rounds, or when all node operators report zero simultaneously. This is a documented edge case in Chainlink's own integration guides. The negative-price scenario is rarer but is the reason Chainlink uses `int256` rather than `uint256` for the answer field. The zero-price path is the more realistic trigger; the negative-price path is lower probability but catastrophic. The missing guard is a single-line omission that the same codebase already corrects elsewhere, making the root cause unambiguously in-scope.

---

### Recommendation

Add a non-positive price check in `ChainlinkPriceOracle.getAssetPrice` before the cast, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [6](#0-5) 

---

### Proof of Concept

1. A Chainlink LST/ETH aggregator enters a failed-round state and `latestRoundData` returns `answer = 0`.
2. Any caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control). [7](#0-6) 
3. `_getTotalEthInProtocol` calls `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice` → returns `uint256(0) * 1e18 / 10^decimals = 0`.
4. The affected asset's entire TVL is zeroed out; `newRsETHPrice` drops sharply.
5. If the drop exceeds `pricePercentageLimit`, the code at lines 277–281 of `LRTOracle.sol` pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself. [8](#0-7) 
6. All user deposits and withdrawals are frozen. The protocol remains paused until an admin calls `unpause` on each contract — a privileged action that may be delayed, constituting a temporary freeze of user funds.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-32)
```text
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
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

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
