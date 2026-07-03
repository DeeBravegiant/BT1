### Title
`ChainlinkPriceOracle.getAssetPrice` Does Not Validate Zero Price, Causing Silent Fund Loss on Deposit and rsETH Price Corruption - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice` fetches `int256 price` from Chainlink's `latestRoundData` and casts it directly to `uint256` without checking `price <= 0`. When Chainlink returns 0, the function silently returns 0. Downstream callers — `LRTOracle._getTotalEthInProtocol` and `LRTDepositPool.getRsETHAmountToMint` — consume this 0 price without any zero-check, producing two distinct impacts: (1) depositors who pass `minRSETHAmountExpected = 0` lose their deposited assets while receiving 0 rsETH, and (2) the rsETH price stored in `LRTOracle` is corrupted downward, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice` reads the Chainlink feed and returns the raw cast of `int256 price` to `uint256`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

There is no `require(price > 0)` guard. If Chainlink returns 0, the function returns 0. [1](#0-0) 

`LRTOracle.getAssetPrice` is a thin pass-through that also performs no zero-check:

```solidity
// contracts/LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [2](#0-1) 

**Path 1 — Deposit minting:** `LRTDepositPool.getRsETHAmountToMint` multiplies the deposit amount by the asset price and divides by `rsETHPrice`. When `getAssetPrice` returns 0, `rsethAmountToMint` is 0:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`_beforeDeposit` only reverts if `rsethAmountToMint < minRSETHAmountExpected`. A depositor who passes `minRSETHAmountExpected = 0` (or is unaware of the oracle failure) will have their ERC-20 tokens transferred in via `safeTransferFrom` and receive 0 rsETH — a permanent loss of deposited funds. [4](#0-3) 

**Path 2 — rsETH price corruption:** `LRTOracle._getTotalEthInProtocol` iterates over all supported assets and accumulates `totalAssetAmt.mulWad(assetER)`. When `assetER` is 0 for any asset, that asset's entire TVL contribution is silently zeroed:

```solidity
// contracts/LRTOracle.sol L339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [5](#0-4) 

`_updateRsETHPrice` then computes `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply` using this deflated `totalETHInProtocol`, writing a permanently lower `rsETHPrice` to storage. This corrupts the exchange rate used for all future deposits and withdrawals. [6](#0-5) 

---

### Impact Explanation

- **Critical / High — Theft of deposited funds:** Any depositor calling `depositAsset` or `depositETH` with `minRSETHAmountExpected = 0` during a zero-price event transfers their assets to the protocol and receives 0 rsETH. The assets are permanently locked in the protocol with no corresponding claim token.
- **High — rsETH price corruption / holder dilution:** A corrupted (deflated) `rsETHPrice` stored in `LRTOracle` means subsequent depositors mint rsETH at an artificially cheap rate, diluting all existing rsETH holders. This constitutes theft of unclaimed yield and protocol insolvency risk.

---

### Likelihood Explanation

Chainlink feeds can return 0 in documented edge cases: a newly deployed feed before its first answer is written, a feed that has been deprecated and zeroed out, or a feed whose aggregator has been reset. The `updatePriceOracleFor` admin path does not validate the live price at the time of oracle registration (only `updatePriceOracleForValidated` does), so a zero-returning oracle can be set. Additionally, `updateRSETHPrice` is a public, permissionless function — any external caller can trigger the price update at the worst possible moment. [7](#0-6) [8](#0-7) 

---

### Recommendation

1. **In `ChainlinkPriceOracle.getAssetPrice`:** Add `require(price > 0, "Invalid price")` before the return statement.
2. **In `LRTOracle.getAssetPrice`:** Add a zero-check on the returned price and revert if it is 0.
3. **In `LRTDepositPool.getRsETHAmountToMint`:** Add `require(assetPrice != 0)` before using the price in the mint calculation.
4. **In `LRTOracle._getTotalEthInProtocol`:** Add `require(assetER != 0)` inside the loop before accumulating the asset's ETH value.

---

### Proof of Concept

1. Chainlink's `latestRoundData` for a supported LST asset (e.g., stETH) returns `price = 0` due to an oracle edge case.
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0`.
3. A depositor calls `depositAsset(stETH, 100e18, 0, "")` with `minRSETHAmountExpected = 0`.
4. `getRsETHAmountToMint` computes `(100e18 * 0) / rsETHPrice = 0`.
5. `_beforeDeposit` does not revert because `0 >= 0`.
6. `IERC20(stETH).safeTransferFrom(msg.sender, address(this), 100e18)` executes — 100 stETH is transferred in.
7. `_mintRsETH(0)` mints 0 rsETH to the depositor.
8. The depositor has permanently lost 100 stETH with no rsETH claim.
9. Separately, any caller invokes `updateRSETHPrice()`. `_getTotalEthInProtocol` computes stETH's contribution as `totalStETHDeposits * 0 = 0`, deflating `totalETHInProtocol`. `rsETHPrice` is written to a lower value, diluting all existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L113-119)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L250-251)
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
