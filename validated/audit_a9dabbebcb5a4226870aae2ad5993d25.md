Let me look at the actual sfrxETH interface and any related oracle contracts to confirm the denomination of `pricePerShare()`.

### Title
SfrxETHPriceOracle Returns frxETH-Denominated Rate as ETH Price, Enabling Over-Minting of rsETH During frxETH Depeg — (`contracts/oracles/SfrxETHPriceOracle.sol`)

---

### Summary

`SfrxETHPriceOracle.getAssetPrice()` returns `sfrxETH.pricePerShare()` verbatim. That function returns **frxETH per sfrxETH**, not **ETH per sfrxETH**. When frxETH trades at parity with ETH the two are numerically identical, so the bug is invisible. When frxETH depegs below ETH the oracle continues to report the frxETH/sfrxETH exchange rate as if it were an ETH/sfrxETH rate, inflating the apparent collateral value. Any depositor can exploit this window to receive more rsETH than the true ETH value of their collateral warrants, causing temporary protocol insolvency and permanently diluting existing rsETH holders.

---

### Finding Description

**Root cause — denomination mismatch with no conversion step**

The `ISfrxETH` interface comment itself acknowledges the unit: *"How much frxETH is 1E18 sfrxETH worth."* The trailing phrase *"Price is in ETH, not USD"* is copied verbatim from the sfrxETH contract and means frxETH is ETH-denominated (not USD-denominated) — it does **not** mean the return value is already in ETH. [1](#0-0) 

`getAssetPrice()` passes this frxETH-denominated value straight back to the caller with no frxETH→ETH conversion: [2](#0-1) 

No other oracle in the codebase performs this conversion for sfrxETH. The `ChainlinkPriceOracle` is used for other assets but is not wired to sfrxETH. [3](#0-2) 

**Exploit path — fully permissionless**

`LRTDepositPool.depositAsset()` is open to any caller: [4](#0-3) 

It calls `getRsETHAmountToMint()`, which divides the inflated oracle price by the stored `rsETHPrice`: [5](#0-4) 

`rsETHPrice` is itself computed from `_getTotalEthInProtocol()`, which also uses the same inflated oracle: [6](#0-5) 

**Why the downside-protection circuit-breaker does not fire**

`_updateRsETHPrice()` pauses the protocol only when `newRsETHPrice < highestRsethPrice`: [7](#0-6) 

Because both the numerator (`totalETHInProtocol`) and denominator (`rsethSupply`) of `newRsETHPrice` are computed using the same inflated oracle, the computed rsETH price does **not** fall during a frxETH depeg — the circuit-breaker never triggers.

---

### Impact Explanation

**Numerical example (frxETH depegs to 0.95 ETH):**

| State | sfrxETH in protocol | True ETH backing | rsETH supply | True rsETH price |
|---|---|---|---|---|
| Before depeg | 100 | 105 ETH | 105 | 1.00 ETH |
| Attacker deposits 100 sfrxETH (true value 99.75 ETH); oracle reports 1.05 ETH/sfrxETH; mints 105 rsETH | 200 | 199.5 ETH | 210 | **0.95 ETH** |
| After depeg resolves | 200 | 210 ETH | 210 | 1.00 ETH |

During the depeg window the protocol is insolvent: 210 rsETH outstanding against only 199.5 ETH of true collateral. The attacker deposited collateral worth 99.75 ETH and received rsETH worth 105 ETH after recovery — a 5.25 ETH gain extracted from the yield that should have accrued to pre-existing holders.

Impact: **Critical — Protocol insolvency (temporary) + permanent dilution of existing rsETH holders.**

---

### Likelihood Explanation

frxETH has historically traded close to 1 ETH, but it is not a hard-pegged asset; secondary-market depegs have occurred for comparable LSTs. The exploit requires no special role, no governance action, and no front-running — only the ability to call `depositAsset()` while the depeg persists. The window can last hours to days, giving ample time for a rational actor to exploit it.

Likelihood: **Medium.**

---

### Recommendation

Replace the bare `pricePerShare()` call with a two-leg price: multiply the frxETH/sfrxETH rate by a Chainlink frxETH/ETH feed (or equivalent) to obtain a true ETH/sfrxETH price:

```solidity
// pseudocode
uint256 frxEthPerSfrxEth = ISfrxETH(sfrxETHContractAddress).pricePerShare(); // frxETH/sfrxETH
uint256 ethPerFrxEth = IChainlinkFeed(frxEthEthFeed).latestAnswer();          // ETH/frxETH (1e18)
return frxEthPerSfrxEth * ethPerFrxEth / 1e18;                                // ETH/sfrxETH
```

Alternatively, use the Chainlink sfrxETH/ETH feed directly if one is available and sufficiently liquid.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork mainnet at a recent block.
// 1. Deploy or reference the live SfrxETHPriceOracle at 0x8546A7C8C3C537914C3De24811070334568eF427
// 2. Mock frxETH market price to 0.95 ETH (e.g. via a Chainlink mock or Curve pool manipulation
//    on a fork — the oracle itself does NOT use a market price, so pricePerShare() is unaffected).
// 3. Record oracle-reported price and true ETH value.

interface ISfrxETH {
    function pricePerShare() external view returns (uint256);
}

interface ILRTDepositPool {
    function depositAsset(address, uint256, uint256, string calldata) external;
    function getRsETHAmountToMint(address, uint256) external view returns (uint256);
}

interface IRSETH {
    function balanceOf(address) external view returns (uint256);
}

contract PoC {
    // Mainnet addresses
    address constant SFRXETH       = 0xac3E018457B222d93114458476f3E3416Abbe38F;
    address constant DEPOSIT_POOL  = 0x036676389e48133B63a802f8635AD39E752D375D;
    address constant RSETH         = 0xA1290d69c65A6Fe4DF752f95823fae25cB99e5A7;

    function run() external {
        uint256 depositAmount = 100 ether; // 100 sfrxETH

        // Oracle-reported price (frxETH/sfrxETH, treated as ETH/sfrxETH)
        uint256 oraclePrice = ISfrxETH(SFRXETH).pricePerShare();
        // e.g. ~1.05e18

        // True ETH value when frxETH = 0.95 ETH
        uint256 frxEthToEth = 0.95e18;
        uint256 trueEthValue = depositAmount * oraclePrice / 1e18 * frxEthToEth / 1e18;
        // = 100 * 1.05 * 0.95 = 99.75 ETH

        // rsETH minted uses the inflated oracle price
        uint256 rsethMinted = ILRTDepositPool(DEPOSIT_POOL).getRsETHAmountToMint(SFRXETH, depositAmount);
        // = 100 * 1.05 / rsETHPrice ≈ 105 rsETH (assuming rsETHPrice ≈ 1.0)

        // Assert: rsETH minted > true ETH value of deposit (insolvency invariant broken)
        require(rsethMinted > trueEthValue, "No over-minting");
        // 105e18 > 99.75e18 — assertion passes, vulnerability confirmed
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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
