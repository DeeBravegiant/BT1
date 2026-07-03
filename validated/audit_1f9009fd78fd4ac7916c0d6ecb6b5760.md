### Title
Unsafe `int256` → `uint256` Typecasting in Chainlink Price Oracle Without Negativity Check - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` directly casts the `int256 price` returned by Chainlink's `latestRoundData()` to `uint256` without first verifying that the value is positive. If Chainlink returns zero or a negative price, the cast silently produces either `0` or an astronomically large number, corrupting every downstream calculation that depends on asset pricing — including rsETH minting and protocol TVL accounting. The same codebase already applies the correct guard in `ChainlinkOracleForRSETHPoolCollateral.sol`, making this an inconsistency with a concrete impact path.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` reads the Chainlink price feed and immediately casts the signed result to unsigned:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no guard on the sign or zero-ness of `price` before the cast. Chainlink's `AggregatorV3Interface` specifies `int256` as the return type precisely because the protocol does not guarantee a positive value in all circuit-breaker or degraded-feed scenarios.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.sol` — another oracle wrapper in the same repository — explicitly rejects non-positive prices before performing the identical cast:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(...decimals());
``` [2](#0-1) 

The unguarded `ChainlinkPriceOracle` is the price source wired into `LRTOracle.getAssetPrice()`:

```solidity
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [3](#0-2) 

`LRTOracle.getAssetPrice()` is consumed in two critical paths:

**Path 1 — Deposit minting:**
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

**Path 2 — Protocol TVL / rsETH price update:**
```solidity
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [5](#0-4) 

---

### Impact Explanation

**Scenario A — Chainlink returns `price = 0`** (documented historical occurrence on degraded feeds):

`uint256(0) = 0`. `getAssetPrice()` returns `0`. In Path 1, `rsethAmountToMint = 0`. A user who deposits LST tokens with `minRSETHAmountExpected = 0` (the default for many integrations) has their tokens transferred into the protocol and receives zero rsETH in return. Their funds are locked with no receipt token to redeem them until the oracle recovers and an admin intervenes. In Path 2, the asset's ETH contribution to TVL is zeroed, causing `newRsETHPrice` to drop, potentially triggering the downside-protection auto-pause of `LRTDepositPool` and `LRTWithdrawalManager`.

**Scenario B — Chainlink returns `price < 0`:**

`uint256(negative_int256)` produces a value ≥ `2^255`. The subsequent multiplication `uint256(price) * 1e18` overflows and reverts under Solidity 0.8.x checked arithmetic. Every call to `getAssetPrice()`, `updateRSETHPrice()`, and `depositAsset()` reverts, freezing deposits and price updates until the feed recovers.

**Impact classification: Medium — Temporary freezing of funds** (Scenario A causes user deposits to be accepted with 0 rsETH minted; Scenario B causes a protocol-wide deposit/price-update DoS).

---

### Likelihood Explanation

Chainlink returning zero or a negative price is not purely theoretical. It has occurred on mainnet during feed deprecations, circuit-breaker activations, and sequencer outages on L2s. The protocol already acknowledges this risk by guarding the identical cast in `ChainlinkOracleForRSETHPoolCollateral.sol`. The unguarded path in `ChainlinkPriceOracle.sol` is reachable by any unprivileged depositor calling `depositAsset()` or by any caller of the public `updateRSETHPrice()`.

---

### Recommendation

Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()` before casting, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, add a staleness check (`updatedAt` / `answeredInRound`) consistent with `ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–31. [6](#0-5) 

---

### Proof of Concept

1. Chainlink's LST/ETH feed enters a degraded state and returns `price = 0`.
2. Any caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `getAssetPrice(lstAsset)` → `ChainlinkPriceOracle.getAssetPrice()` → returns `uint256(0) * 1e18 / decimals = 0`.
4. The affected asset's ETH value is excluded from `totalETHInProtocol`, causing `newRsETHPrice` to be artificially low.
5. Simultaneously, a user calls `depositAsset(lstAsset, 1e18, 0)` with `minRSETHAmountExpected = 0`.
6. `getRsETHAmountToMint()` returns `(1e18 * 0) / rsETHPrice = 0`.
7. `_beforeDeposit` passes (0 ≥ 0), the user's 1 LST is transferred in, and `_mintRsETH(0)` is called — the user receives no rsETH.
8. The user's LST is now locked in the protocol with no receipt token. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-31)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-34)
```text
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
