Audit Report

## Title
Stale Chainlink Price Consumed Without Validation Enables Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation fields (`roundId`, `updatedAt`, `answeredInRound`, and the sign of `price`). A stale or incomplete Chainlink round is consumed as a valid price and fed directly into rsETH minting math. The same repository already implements the correct validation pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming the omission is unintentional.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads the Chainlink feed with:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values are available but four are silently discarded. No check is made for:
- `answeredInRound < roundId` — round not yet answered (stale)
- `updatedAt == 0` — incomplete round
- `price <= 0` — invalid or negative price
- `block.timestamp - updatedAt > heartbeat` — data too old

The same repository already implements all three of these checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unchecked price flows through two unprivileged public paths:

**Deposit minting path:** `depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

**rsETH price update path:** `updateRSETHPrice()` (public, no access control) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()`:

```solidity
uint256 assetER = getAssetPrice(asset);
...
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only applies to the rsETH price update path and only when `pricePercentageLimit > 0` (it defaults to 0). It provides no protection on the deposit minting path. The `minRSETHAmountExpected` slippage parameter in `depositAsset()` is attacker-controlled and can be set to 0.

## Impact Explanation
**High — Theft of unclaimed yield.**

If a Chainlink feed returns a stale, inflated price for an LST (e.g., the LST has dropped in value but the oracle has not updated), a depositor calling `depositAsset()` receives rsETH calculated at the old higher price:

```
rsethAmountToMint = (depositAmount * staleHighPrice) / rsETHPrice
```

The depositor receives more rsETH than the deposited assets are worth, diluting every existing rsETH holder's share of the underlying pool — a direct extraction of accrued yield from existing holders. Note: the claim of "protocol insolvency" from a negative `price` is not valid in Solidity 0.8.x, as `uint256(negativeInt256) * 1e18` would overflow and revert under checked arithmetic. The concrete in-scope impact is **High: Theft of unclaimed yield**.

## Likelihood Explanation
**Medium.** Chainlink oracles return stale data during network congestion, sequencer downtime (relevant for any L2 deployment), or when neither the deviation threshold nor the heartbeat has triggered. This is a well-documented, recurring real-world condition. The attack requires no privileged access — any depositor can call `depositAsset()` or `depositETH()` at the moment a stale price is live, with `minRSETHAmountExpected` set to 0.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
```

A per-feed configurable `STALENESS_THRESHOLD` should be set to match each feed's documented heartbeat interval.

## Proof of Concept
1. Chainlink's stETH/ETH feed enters a stale round (e.g., sequencer downtime on an L2 deployment). `updatedAt` is 2+ hours old; the real stETH price has dropped 5% but the feed still reports the old higher price.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")` with `minRSETHAmountExpected = 0`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. `latestRoundData()` returns the stale inflated price; no revert occurs because `updatedAt`, `answeredInRound`, and `price` sign are never checked.
5. `rsethAmountToMint = (amount * stalePrice) / rsETHPrice` — attacker receives ~5% more rsETH than the deposited stETH is worth.
6. Attacker redeems rsETH, extracting value from existing holders.

**Foundry fork test plan:** Fork mainnet (or an L2) at a block where a Chainlink LST/ETH feed has a stale `updatedAt`. Mock `latestRoundData()` to return an inflated price with `answeredInRound < roundId`. Call `depositAsset()` and assert that `rsethAmountToMint` exceeds the fair value. Confirm no revert occurs in the current code, and that adding the validation checks causes a revert.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
