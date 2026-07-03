### Title
`SfrxETHPriceOracle` Returns sfrxETH/frxETH Rate Instead of sfrxETH/ETH Rate, Enabling Over-Minting of rsETH - (File: contracts/oracles/SfrxETHPriceOracle.sol)

---

### Summary

`SfrxETHPriceOracle.getAssetPrice()` calls `ISfrxETH.pricePerShare()`, which returns the amount of **frxETH** per sfrxETH (the ERC4626 vault share price), not the amount of **ETH** per sfrxETH. Because frxETH is a distinct token that can trade at a discount to ETH on secondary markets, the oracle systematically overstates the sfrxETH/ETH exchange rate. This inflated rate propagates into both rsETH minting and the rsETH price calculation, allowing a depositor to receive more rsETH than the true ETH value of their sfrxETH, diluting all existing rsETH holders and risking protocol insolvency.

---

### Finding Description

`SfrxETHPriceOracle` is the price oracle registered for sfrxETH in `LRTOracle.assetPriceOracle`. Its `getAssetPrice()` implementation is:

```solidity
// contracts/oracles/SfrxETHPriceOracle.sol
function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) {
        revert InvalidAsset();
    }
    return ISfrxETH(sfrxETHContractAddress).pricePerShare();
}
```

The interface comment reads:

```solidity
interface ISfrxETH {
    /// @notice How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD
    function pricePerShare() external view returns (uint256);
}
```

The comment "Price is in ETH, not USD" is incorrect. `pricePerShare()` is the standard ERC4626 function that returns the amount of the **underlying asset** — which for sfrxETH is **frxETH**, not ETH — per share. frxETH is Frax's synthetic liquid staking token. While it is designed to be pegged 1:1 to ETH, it is a separate ERC20 token that trades on secondary markets and can depeg. When frxETH trades below 1 ETH (e.g., 0.99 ETH per frxETH during market stress), the oracle returns a value higher than the true sfrxETH/ETH rate:

```
oracle returns:  sfrxETH/frxETH  (e.g., 1.05)
true rate:       sfrxETH/ETH = (sfrxETH/frxETH) × (frxETH/ETH) = 1.05 × 0.99 = 1.0395
```

This inflated price is consumed in two critical paths:

**Path 1 — rsETH minting** (`LRTDepositPool.getRsETHAmountToMint`):

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A depositor of sfrxETH receives `rsethAmountToMint` proportional to the inflated `getAssetPrice(sfrxETH)`, so they receive more rsETH than the true ETH value of their deposit.

**Path 2 — rsETH price update** (`LRTOracle._getTotalEthInProtocol`):

```solidity
// contracts/LRTOracle.sol:339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

The total ETH in the protocol is overstated by the sfrxETH holdings multiplied by the inflated rate, which in turn inflates `rsETHPrice`, compounding the over-minting effect.

This is the direct analog of H-01: just as `CBEth.exchangeRate()` returned cbETH/stakedETH2 instead of cbETH/ETH, `sfrxETH.pricePerShare()` returns sfrxETH/frxETH instead of sfrxETH/ETH.

---

### Impact Explanation

**Impact: Protocol insolvency / theft of yield from existing rsETH holders.**

When frxETH depegs below 1 ETH:
1. An attacker deposits `N` sfrxETH worth `N × 1.0395 ETH` at true market rates.
2. The oracle prices it at `N × 1.05 ETH` (inflated).
3. The attacker receives rsETH worth `N × 1.05 ETH` in rsETH terms.
4. The attacker redeems rsETH for `N × 1.05 ETH` worth of assets, extracting `N × 0.0105 ETH` from the pool at the expense of other holders.

At scale, this constitutes direct theft of yield from existing rsETH holders and, if the depeg is large enough, can render the protocol insolvent (rsETH backed by less ETH than its stated price implies).

---

### Likelihood Explanation

frxETH has historically maintained a close peg to ETH, but secondary-market depegs of 0.5–2% have occurred during periods of market stress or liquidity crises. The vulnerability is always present (not conditional on an exploit setup) and is exploitable by any unprivileged depositor calling `LRTDepositPool.depositAsset(sfrxETH, ...)`. No special access or governance compromise is required.

---

### Recommendation

Replace `pricePerShare()` with a two-leg calculation that accounts for the frxETH/ETH market rate:

```solidity
// Pseudocode
uint256 sfrxETHPerFrxETH = ISfrxETH(sfrxETHContractAddress).pricePerShare(); // frxETH per sfrxETH
uint256 frxETHPerETH = IChainlinkFeed(frxETHEthFeed).latestAnswer();          // frxETH/ETH from Chainlink
return sfrxETHPerFrxETH * frxETHPerETH / 1e18;                                // sfrxETH/ETH
```

Alternatively, use a Chainlink `sfrxETH/ETH` feed directly if one is available, bypassing the intermediate frxETH step entirely. This mirrors the fix applied in Reserve Protocol PR #899 for cbETH, rETH, and ankrETH.

---

### Proof of Concept

1. Observe that `SfrxETHPriceOracle.getAssetPrice(sfrxETH)` calls `sfrxETH.pricePerShare()`. [1](#0-0) 

2. The ERC4626 `pricePerShare()` returns frxETH per sfrxETH, not ETH per sfrxETH, as stated in the interface comment itself: "How much frxETH is 1E18 sfrxETH worth." [2](#0-1) 

3. This price is consumed by `LRTDepositPool.getRsETHAmountToMint` to determine how many rsETH tokens to mint per unit of sfrxETH deposited. [3](#0-2) 

4. The same inflated price is used in `LRTOracle._getTotalEthInProtocol` to compute the total ETH backing rsETH, inflating `rsETHPrice`. [4](#0-3) 

5. When frxETH trades at a discount to ETH (e.g., 0.99 ETH/frxETH), the oracle returns ~1% more than the true sfrxETH/ETH rate. A depositor calling `depositAsset(sfrxETH, amount, 0, "")` receives proportionally more rsETH than the ETH value of their deposit, extracting value from the pool. [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L99-118)
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
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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
