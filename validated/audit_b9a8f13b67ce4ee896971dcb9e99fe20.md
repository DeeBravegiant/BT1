### Title
`SfrxETHPriceOracle` Returns sfrxETH/frxETH Rate Instead of sfrxETH/ETH Rate, Mispricing the Asset - (File: contracts/oracles/SfrxETHPriceOracle.sol)

### Summary

`SfrxETHPriceOracle.getAssetPrice()` calls `ISfrxETH.pricePerShare()`, which returns the amount of **frxETH** per sfrxETH share — not the amount of ETH per sfrxETH. Because frxETH is not ETH (it trades at a persistent discount on secondary markets), the oracle systematically overstates the ETH value of sfrxETH. This incorrect price propagates into rsETH minting and the rsETH price update, causing existing rsETH holders to be diluted by sfrxETH depositors who receive more rsETH than they are entitled to.

### Finding Description

`SfrxETHPriceOracle` is the registered price oracle for sfrxETH in `LRTOracle`. Its `getAssetPrice()` implementation is:

```solidity
// contracts/oracles/SfrxETHPriceOracle.sol
interface ISfrxETH {
    /// @notice How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD
    function pricePerShare() external view returns (uint256);
}

function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) {
        revert InvalidAsset();
    }
    return ISfrxETH(sfrxETHContractAddress).pricePerShare();
}
```

The inline comment "Price is in ETH, not USD" is incorrect. The actual sfrxETH contract is an ERC4626 vault whose underlying asset is **frxETH**, not ETH. `pricePerShare()` returns the sfrxETH→frxETH exchange rate. frxETH is Frax's ETH-pegged liquid staking token that trades at a persistent discount to ETH on secondary markets (historically 0.05%–2%). The oracle therefore returns a price that is slightly higher than the true sfrxETH/ETH rate.

This is structurally identical to the Lido adapter bug in the reference report: `getStETHByWstETH()` returned WstETH→stETH, not WstETH→ETH, because stETH ≠ ETH. Here, `pricePerShare()` returns sfrxETH→frxETH, not sfrxETH→ETH, because frxETH ≠ ETH.

### Impact Explanation

The incorrect price flows through two critical paths:

**Path 1 — rsETH minting for sfrxETH depositors:**

`LRTDepositPool.getRsETHAmountToMint()` computes:
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(sfrxETH)) / lrtOracle.rsETHPrice()
```
Since `getAssetPrice(sfrxETH)` is overstated (sfrxETH/frxETH > sfrxETH/ETH), sfrxETH depositors receive more rsETH than the ETH value they contributed warrants. This dilutes all existing rsETH holders.

**Path 2 — rsETH price update:**

`LRTOracle._getTotalEthInProtocol()` sums `totalAssetAmt * getAssetPrice(asset)` for all supported assets. The sfrxETH contribution is overstated, inflating `totalETHInProtocol`, which inflates `newRsETHPrice`. This creates a feedback loop: the inflated rsETH price partially offsets the minting overcount, but the net effect is that the protocol's accounting of its ETH backing is incorrect.

The impact is classified as **Low** (contract fails to deliver promised returns to existing rsETH holders without direct fund loss), consistent with the reference report's Medium downgrade reasoning. The magnitude of the frxETH/ETH discount is small but persistent and non-zero.

### Likelihood Explanation

Likelihood is **High**. sfrxETH is a listed supported asset with a deployed oracle at `0x8546A7C8C3C537914C3De24811070334568eF427`. Every call to `depositAsset(sfrxETH, ...)` and every call to `updateRSETHPrice()` exercises the vulnerable code path. No special conditions or attacker actions are required — the mispricing is structural and continuous.

### Recommendation

`getAssetPrice()` must return the sfrxETH/ETH rate, not the sfrxETH/frxETH rate. The correct two-step conversion is:

```
sfrxETH/ETH = sfrxETH/frxETH * frxETH/ETH
```

The frxETH/ETH rate can be obtained from a Chainlink feed (frxETH/ETH is available) or from the Curve frxETH/ETH pool's `get_dy` / `price_oracle`. The oracle should be updated to compose both rates:

```solidity
function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) revert InvalidAsset();
    uint256 sfrxEthToFrxEth = ISfrxETH(sfrxETHContractAddress).pricePerShare();
    uint256 frxEthToEth = IFrxEthOracle(frxEthOracle).getRate(); // frxETH/ETH
    return sfrxEthToFrxEth * frxEthToEth / 1e18;
}
```

### Proof of Concept

1. sfrxETH is a supported asset in `LRTConfig` with `SfrxETHPriceOracle` registered in `LRTOracle`.
2. A user calls `LRTDepositPool.depositAsset(sfrxETH, 1e18, 0, "")`.
3. `_beforeDeposit` calls `getRsETHAmountToMint(sfrxETH, 1e18)`.
4. This calls `lrtOracle.getAssetPrice(sfrxETH)` → `SfrxETHPriceOracle.getAssetPrice(sfrxETH)` → `pricePerShare()`.
5. `pricePerShare()` returns, e.g., `1.08e18` (sfrxETH/frxETH rate).
6. The true sfrxETH/ETH rate is `1.08e18 * 0.998e18 / 1e18 = 1.0778e18` (accounting for frxETH discount).
7. The user receives `1.08e18 / rsETHPrice` rsETH instead of the correct `1.0778e18 / rsETHPrice`.
8. The ~0.2% excess rsETH minted dilutes all existing rsETH holders proportionally.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
