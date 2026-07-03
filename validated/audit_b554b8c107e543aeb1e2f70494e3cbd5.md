All code references verified. The claim is accurate and the exploit path is sound.

Audit Report

## Title
`SfrxETHPriceOracle.getAssetPrice()` Returns frxETH/sfrxETH Instead of ETH/sfrxETH, Overvaluing sfrxETH Collateral During frxETH Depeg — (`contracts/oracles/SfrxETHPriceOracle.sol`)

## Summary

`SfrxETHPriceOracle.getAssetPrice()` returns `sfrxETH.pricePerShare()` directly, which is denominated in **frxETH per sfrxETH**, not ETH per sfrxETH. The protocol consumes this value as an ETH exchange rate throughout `_getTotalEthInProtocol()` and `getRsETHAmountToMint()`. When frxETH trades below ETH parity, sfrxETH collateral is systematically overvalued, allowing sfrxETH depositors to mint excess rsETH that dilutes the yield accrued by existing ETH and other-asset holders.

## Finding Description

`SfrxETHPriceOracle.getAssetPrice()` delegates entirely to `pricePerShare()`: [1](#0-0) 

The interface comment is self-contradictory — it correctly states the return unit is frxETH ("How much frxETH is 1E18 sfrxETH worth") but then incorrectly asserts "Price is in ETH, not USD": [2](#0-1) 

sfrxETH is an ERC-4626 vault whose underlying asset is frxETH, not ETH. `pricePerShare()` returns frxETH per sfrxETH. frxETH is a separate token that can trade at a discount to ETH on secondary markets.

This inflated value flows into `_getTotalEthInProtocol()` without any frxETH/ETH conversion: [3](#0-2) 

`rsETHPrice` is then computed from this inflated total: [4](#0-3) 

rsETH minting uses both the inflated asset price and the inflated `rsETHPrice`: [5](#0-4) 

**Why the inflation does not cancel out:** When the protocol holds a mix of ETH and sfrxETH, the inflated `pricePerShare()` raises `totalETHInProtocol` (and thus `rsETHPrice`) by a factor proportional to the sfrxETH share of TVL. A new sfrxETH depositor's numerator (`getAssetPrice(sfrxETH)`) is inflated by the full `pricePerShare()`, while the denominator (`rsETHPrice`) is only partially inflated (diluted by the ETH portion). The sfrxETH depositor therefore receives more rsETH than their true ETH-equivalent contribution warrants, at the expense of existing holders.

**Concrete example** (50 ETH + 50 sfrxETH, `pricePerShare = 1.05e18`, frxETH at 0.99 ETH, 100 rsETH supply):

| | Oracle (inflated) | True |
|---|---|---|
| sfrxETH ETH value | 1.05 | 1.0395 |
| totalETHInProtocol | 102.5 | 101.975 |
| rsETHPrice | 1.025 | 1.01975 |
| rsETH minted per 1 sfrxETH | 1.02439 | 1.01937 |
| rsETH minted per 1 ETH | 0.97561 | 0.98063 |

Each sfrxETH deposit during a depeg mints ~0.005 excess rsETH, diluting all existing holders.

No existing guard prevents this. The `pricePercentageLimit` check in `_updateRsETHPrice()` compares the new price against `highestRsethPrice` and would only trigger on a sudden spike, not on the systematic overvaluation baked in by the oracle design. The deposit flow reads the stored `rsETHPrice` (already inflated by the same oracle) and calls `getAssetPrice()` live, so both sides of the minting formula are affected but asymmetrically.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders have accrued yield reflected in `rsETHPrice > 1e18`. Each sfrxETH deposit during a frxETH depeg mints excess rsETH, diluting the per-token ETH backing of all existing rsETH. This is a direct, quantifiable transfer of accrued yield from existing holders to the sfrxETH depositor. The magnitude scales with the frxETH/ETH depeg depth and the proportion of sfrxETH TVL. At 1% depeg and 50% sfrxETH TVL share, the overstatement is ~50 bps per deposit, compounding with each subsequent sfrxETH deposit.

## Likelihood Explanation

- sfrxETH is a named, first-class supported asset (`SFRX_ETH_TOKEN` in `LRTConstants`). [6](#0-5) 
- The exploit path is fully permissionless: any user calls `depositAsset(sfrxETH, amount, minRSETH, referralId)` during a frxETH depeg event.
- No admin compromise, front-running, governance capture, or victim mistake is required.
- frxETH has historically maintained a tight peg but has traded at discounts during market stress. Even a 0.5% depeg produces measurable yield theft at scale.
- The oracle design flaw is always present; the depeg is the only external precondition.

**Likelihood: Medium** (requires frxETH to depeg, a realistic but not constant condition).

## Recommendation

Compose `pricePerShare()` with a Chainlink frxETH/ETH price feed, mirroring how `ChainlinkPriceOracle` handles other assets: [7](#0-6) 

```solidity
// In SfrxETHPriceOracle.getAssetPrice():
uint256 frxEthPerSfrxEth = ISfrxETH(sfrxETHContractAddress).pricePerShare();
(, int256 frxEthEthPrice,,,) = AggregatorV3Interface(frxEthEthFeed).latestRoundData();
uint256 ethPerFrxEth = uint256(frxEthEthPrice) * 1e18 / 10 ** frxEthEthFeedDecimals;
return frxEthPerSfrxEth * ethPerFrxEth / 1e18;
```

This eliminates the implicit frxETH ≈ ETH assumption and produces a true ETH-denominated rate for sfrxETH.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;
// forge test --fork-url $ETH_RPC_URL --match-test testSfrxETHOracleOvervaluation -vvv

import "forge-std/Test.sol";

interface ISfrxETH { function pricePerShare() external view returns (uint256); }

contract SfrxETHOraclePoCTest is Test {
    address constant SFRXETH = 0xac3E018457B222d93114458476f3E3416Abbe38F;

    function testSfrxETHOracleOvervaluation() public {
        uint256 pricePerShare = ISfrxETH(SFRXETH).pricePerShare();

        // Protocol treats pricePerShare as ETH/sfrxETH
        uint256 oracleReportedEthValue = pricePerShare;

        // Simulate frxETH at 0.99 ETH (1% depeg)
        uint256 frxEthToEth = 0.99e18;
        uint256 trueEthValue = pricePerShare * frxEthToEth / 1e18;

        uint256 overvaluation = oracleReportedEthValue - trueEthValue;

        // Mixed protocol: 50 ETH + 50 sfrxETH, 100 rsETH supply
        uint256 ethInProtocol = 50e18;
        uint256 sfrxEthInProtocol = 50e18;

        uint256 totalETHOracle = ethInProtocol + sfrxEthInProtocol * oracleReportedEthValue / 1e18;
        uint256 totalETHTrue   = ethInProtocol + sfrxEthInProtocol * trueEthValue / 1e18;

        uint256 rsethSupply = 100e18;
        uint256 rsETHPriceOracle = totalETHOracle * 1e18 / rsethSupply;
        uint256 rsETHPriceTrue   = totalETHTrue   * 1e18 / rsethSupply;

        // New depositor: 1 sfrxETH
        uint256 mintedOracle = 1e18 * oracleReportedEthValue / rsETHPriceOracle;
        uint256 mintedTrue   = 1e18 * trueEthValue           / rsETHPriceTrue;

        emit log_named_uint("Excess rsETH minted per sfrxETH (wei)", mintedOracle - mintedTrue);
        // ~0.005e18 excess rsETH — dilutes all existing holders

        assertGt(mintedOracle, mintedTrue, "No excess minting when frxETH depegs");
    }
}
```

### Citations

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L8-11)
```text
interface ISfrxETH {
    /// @notice How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD
    function pricePerShare() external view returns (uint256);
}
```

**File:** contracts/oracles/SfrxETHPriceOracle.sol (L35-41)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != sfrxETHContractAddress) {
            revert InvalidAsset();
        }

        return ISfrxETH(sfrxETHContractAddress).pricePerShare();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
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

**File:** contracts/utils/LRTConstants.sol (L10-10)
```text
    bytes32 public constant SFRX_ETH_TOKEN = keccak256("SFRX_ETH_TOKEN");
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```
