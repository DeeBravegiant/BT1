### Title
Stale Chainlink Price Accepted Without `updatedAt` Validation Causes Incorrect Asset Accounting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but silently discards the `updatedAt` timestamp, accepting arbitrarily stale prices. This is the direct analog of the external report's root cause: a settlement/accounting function uses a price that does not reflect the true value at the relevant point in time. In LRT-rsETH, a stale asset price propagates into `LRTWithdrawalManager.getExpectedAssetAmount()`, which is called live during `instantWithdrawal()` and `initiateWithdrawal()`, allowing a user to receive more underlying asset than their rsETH is actually worth.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink round data but only destructures the `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The `updatedAt` field — which records when the price was last pushed on-chain — is completely ignored. No staleness threshold (e.g., `block.timestamp - updatedAt > MAX_DELAY`) is enforced.

This oracle is the authoritative price source for all supported LST assets (stETH, ETHx, etc.) in the core L1 system. `LRTOracle.getAssetPrice()` delegates directly to it:

```solidity
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

`LRTWithdrawalManager.getExpectedAssetAmount()` calls this live at withdrawal time:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

This value is used directly in both `initiateWithdrawal()` and `instantWithdrawal()`.

By contrast, the pool-side oracle wrapper `ChainlinkOracleForRSETHPoolCollateral` does perform partial validation (`answeredInRound < roundID`, `timestamp == 0`), but the core L1 `ChainlinkPriceOracle` performs none.

---

### Impact Explanation

**Scenario — stale lower asset price during `instantWithdrawal()`:**

If a Chainlink feed for an LST (e.g., stETH/ETH) goes stale and its last reported price is lower than the true market price:

- `getAssetPrice(stETH)` returns a stale, deflated value
- `getExpectedAssetAmount(stETH, rsETHUnstaked)` = `rsETHUnstaked × rsETHPrice / staleLowAssetPrice` → inflated asset amount
- The user receives more stETH than their rsETH is actually worth
- The excess comes from other depositors' share of the protocol TVL

This is **theft of user funds** (other depositors' underlying assets are drained). The `instantWithdrawal()` path is callable by any unprivileged user holding rsETH.

**Scenario — stale lower asset price during `initiateWithdrawal()`:**

`expectedAssetAmount` is locked in at initiation using the stale price. When `unlockQueue()` later settles with `_calculatePayoutAmount()` using `min(expectedAssetAmount, currentReturn)`, the inflated `expectedAssetAmount` may still be paid out if the current return is also inflated (because `_createUnlockParams()` also calls `lrtOracle.getAssetPrice()` live at unlock time, which may still be stale).

**Severity: High** — theft of unclaimed yield / temporary theft of underlying assets from the protocol.

---

### Likelihood Explanation

Chainlink feeds can go stale due to:
- L1 network congestion preventing keeper transactions
- Chainlink node outages
- Heartbeat-only feeds not updating during low-volatility periods

The `ChainlinkPriceOracle` is deployed on Ethereum mainnet where these conditions are realistic. No keeper or admin action is required to trigger the vulnerability — the attacker simply monitors the `updatedAt` field off-chain and calls `instantWithdrawal()` when a feed is stale.

---

### Recommendation

Add a staleness check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();
if (price <= 0) revert InvalidPrice();
```

The `MAX_STALENESS_PERIOD` should be set per-asset based on the Chainlink feed's documented heartbeat (e.g., 24 hours for stETH/ETH). This pattern is already partially implemented in `ChainlinkOracleForRSETHPoolCollateral` and should be consistently applied to the core L1 oracle.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed goes stale (last `updatedAt` = T-25h, true price = 1.05 ETH, stale price = 1.00 ETH).
2. Attacker holds 100 rsETH (worth ~105 stETH at true prices, assuming rsETHPrice = 1.05 ETH).
3. Attacker calls `instantWithdrawal(stETH, 100e18, "")`.
4. `getExpectedAssetAmount(stETH, 100e18)` = `100e18 × 1.05e18 / 1.00e18` = 105 stETH (correct by coincidence here, but if stale price is 0.95 ETH: `100e18 × 1.05e18 / 0.95e18` ≈ 110.5 stETH).
5. Attacker receives ~110.5 stETH instead of ~105 stETH — ~5.5 stETH stolen from other depositors.
6. `ChainlinkPriceOracle.getAssetPrice()` never checked `updatedAt`, so the stale price passed through unchallenged.

**Root cause line:** [1](#0-0) 

**Propagation into withdrawal accounting:** [2](#0-1) 

**`instantWithdrawal()` entry point (unprivileged):** [3](#0-2) 

**`LRTOracle.getAssetPrice()` delegation:** [4](#0-3) 

**Contrast — pool oracle does partial validation, core oracle does none:** [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTWithdrawalManager.sol (L212-228)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
