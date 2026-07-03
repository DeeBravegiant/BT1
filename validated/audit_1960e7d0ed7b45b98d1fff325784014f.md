### Title
Unchecked `int256`-to-`uint256` Cast of Chainlink Price Causes Inflated rsETH Price and Protocol Insolvency - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` casts the `int256 answer` returned by Chainlink's `latestRoundData()` directly to `uint256` without verifying it is non-negative. A negative price wraps to an astronomically large value, which propagates through `LRTOracle._getTotalEthInProtocol()` into `rsETHPrice`, corrupting the protocol's exchange rate and enabling insolvency.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` performs an unchecked signed-to-unsigned cast:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

If `price` is negative (e.g., `-1`), `uint256(-1)` evaluates to `2^256 - 1`, an astronomically large value. There is no `price > 0` guard anywhere in this function.

By contrast, the sibling oracle `ChainlinkOracleForRSETHPoolCollateral` explicitly rejects non-positive prices:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L32
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The corrupted value flows through the following call chain:

1. `LRTOracle.getAssetPrice()` delegates to `ChainlinkPriceOracle.getAssetPrice()`: [3](#0-2) 

2. `LRTOracle._getTotalEthInProtocol()` accumulates `assetER` (the corrupted price) into `totalETHInProtocol`: [4](#0-3) 

3. `_updateRsETHPrice()` computes `newRsETHPrice` from the inflated `totalETHInProtocol`: [5](#0-4) 

4. If `pricePercentageLimit == 0` (its default, since `initialize()` never sets it), the threshold guard is skipped and `rsETHPrice` is written with the corrupted value: [6](#0-5) [7](#0-6) 

5. `updateRSETHPrice()` is a public, permissionless function — any caller can trigger the update: [8](#0-7) 

---

### Impact Explanation

**Critical — Protocol insolvency / share mis-accounting.**

Once `rsETHPrice` is set to a near-`2^256` value:

- **Depositors** calling `depositAsset()` or `depositETH()` receive near-zero rsETH for their assets, because the mint calculation divides by the inflated price.
- **Existing rsETH holders** can redeem their shares for a vastly disproportionate amount of underlying assets, draining the protocol.

The `LRTDepositPool` uses `rsETHPrice` (via `LRTOracle`) to determine how many rsETH tokens to mint per deposited asset: [9](#0-8) 

---

### Likelihood Explanation

**Low.** Chainlink price feeds for LST/ETH pairs (stETH/ETH, rETH/ETH, etc.) do not routinely return negative values under normal operation. However, Chainlink's `latestRoundData()` is documented to return `0` or negative answers during feed deprecation, circuit-breaker events, or if an incorrect feed address is configured. The missing validation is a well-known Chainlink integration best practice, and the same codebase already applies it correctly in `ChainlinkOracleForRSETHPoolCollateral`. The inconsistency confirms the omission is unintentional.

---

### Recommendation

Add a non-positive price guard in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

---

### Proof of Concept

1. Chainlink feed for a supported LST asset returns `price = -1` (e.g., during a circuit-breaker event).
2. `ChainlinkPriceOracle.getAssetPrice(asset)` returns `uint256(-1) * 1e18 / 10**decimals` ≈ `2^256 - 1`.
3. `LRTOracle._getTotalEthInProtocol()` accumulates this into `totalETHInProtocol`.
4. `_updateRsETHPrice()` computes `newRsETHPrice ≈ 2^256 / rsethSupply` — an astronomically large value.
5. With `pricePercentageLimit == 0`, the check at line 256–257 is a no-op; `rsETHPrice` is written.
6. Any subsequent depositor calling `depositAsset()` receives ≈ 0 rsETH for their deposit.
7. Any existing rsETH holder calling `requestWithdrawal()` can claim assets far exceeding their fair share, draining the pool.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L99-117)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```
