Audit Report

## Title
Unguarded `int256` → `uint256` Cast in Chainlink Price Oracle Enables Zero-Price Deposit Drain and DoS - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` casts the `int256` result of `latestRoundData()` directly to `uint256` with no positivity check. If Chainlink returns `price = 0`, a depositor calling `depositAsset()` with `minRSETHAmountExpected = 0` will have their LST transferred into the protocol while receiving zero rsETH in return. If Chainlink returns a negative price, the subsequent multiplication overflows under Solidity 0.8.x checked arithmetic, causing a protocol-wide DoS on deposits and price updates. The same codebase already applies the correct guard in `ChainlinkOracleForRSETHPoolCollateral.sol`, confirming the inconsistency.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at lines 52–54, the price is read and immediately cast without any sign or zero check:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` explicitly rejects non-positive prices before the identical cast:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / ...;
``` [2](#0-1) 

`LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle.getAssetPrice()` with no additional guard: [3](#0-2) 

The corrupted price propagates into two critical paths:

**Path 1 — Deposit minting** (`LRTDepositPool.getRsETHAmountToMint`): [4](#0-3) 

**Path 2 — TVL / rsETH price update** (`LRTOracle._getTotalEthInProtocol`): [5](#0-4) 

The slippage guard in `_beforeDeposit` only protects users who explicitly set `minRSETHAmountExpected > 0`: [6](#0-5) 

## Impact Explanation
**Scenario A — `price = 0`:** `getAssetPrice()` returns 0. `getRsETHAmountToMint()` returns 0. Any depositor who passes `minRSETHAmountExpected = 0` (the default in many integrations and aggregators) will have their LST transferred into the protocol while `_mintRsETH(0)` mints zero rsETH. The user holds no receipt token and cannot redeem their deposit until the oracle recovers and an admin intervenes — **temporary freezing of funds (Medium)**.

**Scenario B — `price < 0`:** `uint256(negative_int256)` produces a value ≥ `2^255`. The multiplication `uint256(price) * 1e18` overflows and reverts under Solidity 0.8.x checked arithmetic. Every call to `getAssetPrice()`, `updateRSETHPrice()`, and `depositAsset()` reverts, freezing all deposits and price updates — **temporary freezing of funds (Medium)**.

## Likelihood Explanation
Chainlink returning zero or a negative price is not purely theoretical: it has occurred on mainnet during feed deprecations, circuit-breaker activations, and L2 sequencer outages. The protocol itself acknowledges this risk by guarding the identical cast in `ChainlinkOracleForRSETHPoolCollateral.sol`. `updateRSETHPrice()` is a public function with no access control, and `depositAsset()` is callable by any unprivileged user, making both paths reachable without any special privileges.

## Recommendation
Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()` before casting, mirroring the pattern in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, add staleness checks (`answeredInRound < roundId`, `updatedAt == 0`) consistent with `ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–31. [7](#0-6) 

## Proof of Concept
1. Chainlink's LST/ETH feed enters a degraded state and returns `price = 0`.
2. Any caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `getAssetPrice(lstAsset)` → `ChainlinkPriceOracle.getAssetPrice()` → returns `uint256(0) * 1e18 / decimals = 0`.
4. The affected asset's ETH contribution to TVL is zeroed, causing `newRsETHPrice` to drop artificially.
5. Simultaneously, a user calls `depositAsset(lstAsset, 1e18, 0)` with `minRSETHAmountExpected = 0`.
6. `getRsETHAmountToMint()` returns `(1e18 * 0) / rsETHPrice = 0`.
7. `_beforeDeposit` passes (`0 >= 0`), the user's 1 LST is transferred in, and `_mintRsETH(0)` is called — the user receives no rsETH.
8. The user's LST is locked in the protocol with no receipt token to redeem it.

**Foundry fork test plan:**
```solidity
function testZeroPriceDepositDrain() public {
    // Fork mainnet, mock Chainlink feed to return price = 0
    vm.mockCall(priceFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
        abi.encode(1, int256(0), block.timestamp, block.timestamp, 1));
    uint256 balanceBefore = IERC20(lst).balanceOf(user);
    vm.prank(user);
    depositPool.depositAsset(lst, 1e18, 0, "");
    // Assert: user LST balance decreased, rsETH balance is 0
    assertEq(IERC20(rsETH).balanceOf(user), 0);
    assertLt(IERC20(lst).balanceOf(user), balanceBefore);
}
```

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-34)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
