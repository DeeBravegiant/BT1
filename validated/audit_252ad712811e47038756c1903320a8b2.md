### Title
`SfrxETHPriceOracle` Returns sfrxETH/frxETH Instead of sfrxETH/ETH, Causing Incorrect rsETH Pricing - (`contracts/oracles/SfrxETHPriceOracle.sol`)

---

### Summary

`SfrxETHPriceOracle.getAssetPrice()` calls `pricePerShare()` on the sfrxETH contract and returns the result directly as the sfrxETH/ETH exchange rate. However, `pricePerShare()` returns the sfrxETH/frxETH rate (how many frxETH per sfrxETH), not sfrxETH/ETH. Because frxETH is a separate token that is not guaranteed to be 1:1 with ETH, the wrong quote currency is used — an exact structural analog to H-13.

---

### Finding Description

`SfrxETHPriceOracle` implements `IPriceFetcher` and is registered in `LRTOracle` as the price source for sfrxETH. Its `getAssetPrice()` function is:

```solidity
function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) {
        revert InvalidAsset();
    }
    return ISfrxETH(sfrxETHContractAddress).pricePerShare();
}
```

The interface definition in the same file carries a misleading comment:

```solidity
interface ISfrxETH {
    /// @notice How much frxETH is 1E18 sfrxETH worth. Price is in ETH, not USD
    function pricePerShare() external view returns (uint256);
}
```

The comment says "Price is in ETH" but the actual sfrxETH contract (an ERC-4626 vault) has frxETH as its underlying asset. `pricePerShare()` returns the amount of **frxETH** redeemable per sfrxETH share — i.e., the sfrxETH/frxETH rate. frxETH is Frax's ETH-pegged stablecoin and is a distinct token that can and does trade at a discount or premium to ETH.

The correct sfrxETH/ETH price requires an additional step:

$$
\text{sfrxETH/ETH} = \text{pricePerShare()} \times \frac{\text{frxETH}}{\text{ETH}}
$$

The missing `frxETH/ETH` multiplier is the root cause.

This price is consumed by `LRTOracle._getTotalEthInProtocol()`:

```solidity
uint256 assetER = getAssetPrice(asset);          // returns sfrxETH/frxETH, not sfrxETH/ETH
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

The TVL contribution of sfrxETH is therefore denominated in frxETH, not ETH. The resulting `rsETHPrice` is incorrect. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

`rsETHPrice` is used for every critical protocol operation:

- **Minting**: users depositing sfrxETH receive rsETH priced against the incorrect rate — over-minted when frxETH < ETH, under-minted when frxETH > ETH.
- **Withdrawal accounting**: the rsETH/ETH rate used by `LRTWithdrawalManager` is derived from the same corrupted price.
- **L2 pool swaps**: `RSETHPool.viewSwapRsETHAmountAndFee` uses `getRate()` which reads `rsETHPrice`, so L2 depositors receive incorrect rsETH amounts.
- **External consumers**: `RSETHPriceFeed.latestRoundData()` multiplies `rsETHPrice` by ETH/USD and exposes the error to any DeFi protocol using the Chainlink-compatible feed. [4](#0-3) [5](#0-4) [6](#0-5) 

The magnitude of the error is bounded by the frxETH/ETH peg deviation. Under normal conditions this is small (~0.1–0.5%), but during market stress frxETH has historically depegged by several percent. The impact is classified as **Low** (contract fails to deliver promised returns) under normal conditions, escalating toward **Medium** (temporary fund mis-accounting) during depeg events.

---

### Likelihood Explanation

The bug is always active whenever sfrxETH is a supported asset in `LRTConfig`. Any call to `updateRSETHPrice()` — which is a public, permissionless function — triggers `_getTotalEthInProtocol()` and propagates the incorrect price. No special attacker action is required; the error is structural and continuous. [7](#0-6) 

---

### Recommendation

Replace the direct `pricePerShare()` call with a two-step computation that accounts for the frxETH/ETH price:

```solidity
function getAssetPrice(address asset) external view returns (uint256) {
    if (asset != sfrxETHContractAddress) revert InvalidAsset();

    // sfrxETH/frxETH rate (ERC-4626 pricePerShare)
    uint256 sfrxEthToFrxEth = ISfrxETH(sfrxETHContractAddress).pricePerShare();

    // frxETH/ETH rate from a Chainlink or Curve oracle
    uint256 frxEthToEth = IFrxEthOracle(frxEthOracle).getRate();

    // sfrxETH/ETH = (sfrxETH/frxETH) * (frxETH/ETH) / 1e18
    return sfrxEthToFrxEth * frxEthToEth / 1e18;
}
```

A Chainlink frxETH/ETH feed or a Curve TWAP oracle for the frxETH/ETH pool can supply the missing multiplier.

---

### Proof of Concept

1. Assume frxETH trades at 0.98 ETH (a 2% depeg, which has occurred historically).
2. `pricePerShare()` returns `1.05e18` (sfrxETH/frxETH — 5% yield accrued).
3. `SfrxETHPriceOracle.getAssetPrice(sfrxETH)` returns `1.05e18`.
4. Correct sfrxETH/ETH = `1.05 × 0.98 = 1.029e18`.
5. `_getTotalEthInProtocol()` overvalues sfrxETH by ~2%, inflating `rsETHPrice`.
6. A user depositing ETH receives fewer rsETH than they should (rsETH appears more expensive than it is), while a user who previously deposited sfrxETH holds rsETH backed by an overvalued position — they can withdraw more ETH than their fair share, at the expense of ETH depositors. [2](#0-1) [8](#0-7) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L331-348)
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
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/pools/RSETHPool.sol (L339-346)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
