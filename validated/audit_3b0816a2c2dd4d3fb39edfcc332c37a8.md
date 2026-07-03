### Title
Decimal-Unaware rsETH Minting Calculation Causes Near-Total Loss for Depositors of Non-18-Decimal Assets - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint` multiplies the raw deposited `amount` (in the asset's native decimal precision) directly against an 18-decimal exchange rate without first normalizing the amount to 18 decimals. If a non-18-decimal asset is ever added as a supported asset, depositors of that asset will receive a factor of `10^(18 - assetDecimals)` fewer rsETH than they are owed, effectively losing their entire deposit. The same decimal assumption flaw exists in `LRTOracle._getTotalEthInProtocol`, which would simultaneously understate the protocol TVL, inflating rsETH price for existing holders.

### Finding Description

**Root cause in `LRTDepositPool.getRsETHAmountToMint`:**

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` returns a value in 1e18 precision (e.g., `1e18` for a 1:1 ETH-pegged asset). `lrtOracle.rsETHPrice()` is also in 1e18 precision. The formula therefore implicitly assumes `amount` is also in 1e18 precision (18 decimals). For all currently supported assets (stETH, ETHx, rETH, sfrxETH, swETH) this holds because they are all 18-decimal tokens. However, the function contains no guard against non-18-decimal assets, and `addNewSupportedAsset` (gated by `TIME_LOCK_ROLE`) imposes no decimal check either.

For a hypothetical 8-decimal asset (e.g., WBTC) deposited as `1e8` raw units:
- Actual calculation: `(1e8 × assetPrice) / rsETHPrice`
- Expected calculation: `(1e18 × assetPrice) / rsETHPrice`
- Shortfall factor: `1e10`

**Compounding root cause in `LRTOracle._getTotalEthInProtocol`:**

```solidity
// totalAssetAmt is in 1e18 precision (standard token decimals)  ← incorrect comment
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`getTotalAssetDeposits` returns the raw ERC20 balance via `IERC20(asset).balanceOf(...)`, which is in the asset's native decimal precision. `mulWad` divides by `1e18`, so for an 8-decimal asset the ETH contribution is understated by `1e10`. This causes `rsETHPrice` (computed as `totalETH / rsETHSupply`) to be understated, which in turn causes all subsequent depositors to receive inflated rsETH at the expense of existing holders.

**Same flaw in L2 pools:**

`RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` (and identically in `RSETHPoolV3ExternalBridge` and `RSETHPoolV3WithNativeChainBridge`):

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` from `ChainlinkOracleForRSETHPoolCollateral.getRate()` is normalized to 1e18. `amountAfterFee` is in the token's native decimals. For a 6-decimal token, the user receives `1e12` times fewer wrsETH than owed.

### Impact Explanation

A user depositing 1 WBTC (8 decimals, `1e8` raw units) at a price of 30 ETH per WBTC and rsETH price of 1 ETH:
- Receives: `(1e8 × 30e18) / 1e18 = 30e8` rsETH ≈ `3e-10` rsETH in human-readable terms
- Should receive: `30e18` rsETH = 30 rsETH

The depositor loses their entire 1 WBTC (worth ~30 ETH) and receives a negligible dust amount of rsETH. The 30 ETH of value is permanently locked in the protocol with no recovery path (the withdrawal system uses the same broken accounting). This constitutes **direct theft of depositor funds** — Critical impact.

### Likelihood Explanation

The vulnerability is latent: it is triggered by a legitimate governance action (adding a non-18-decimal asset via `addNewSupportedAsset` with `TIME_LOCK_ROLE`). No malicious actor is required. The protocol has no decimal guard in `addNewSupportedAsset`, `updatePriceOracleFor`, or `getRsETHAmountToMint`. As the protocol expands its asset universe (e.g., WBTC, cbBTC, or any LST with non-standard decimals), this path becomes reachable. Likelihood is **Low** given current asset set, but the code path is fully reachable by any unprivileged depositor once such an asset is added.

### Recommendation

Normalize `amount` to 18 decimals before performing the exchange-rate multiplication in `getRsETHAmountToMint`:

```solidity
function getRsETHAmountToMint(address asset, uint256 amount) public view override returns (uint256 rsethAmountToMint) {
    address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
    ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

    uint8 assetDecimals = IERC20Metadata(asset).decimals();
    uint256 normalizedAmount = amount * (10 ** (18 - assetDecimals));

    rsethAmountToMint = (normalizedAmount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
}
```

Apply the same normalization in `_getTotalEthInProtocol` and in all L2 pool `viewSwapRsETHAmountAndFee(amount, token)` overloads. Alternatively, enforce an 18-decimal requirement in `addNewSupportedAsset` and the L2 pool's `addSupportedToken`.

### Proof of Concept

1. Admin calls `addNewSupportedAsset(WBTC, depositLimit)` (WBTC has 8 decimals).
2. Admin calls `updatePriceOracleFor(WBTC, wbtcOracle)` where `wbtcOracle.getAssetPrice(WBTC)` returns `30e18` (30 ETH per WBTC).
3. User calls `depositAsset(WBTC, 1e8, 0, "")` (depositing 1 WBTC).
4. `_beforeDeposit` calls `getRsETHAmountToMint(WBTC, 1e8)`:
   - `rsethAmountToMint = (1e8 × 30e18) / 1e18 = 30e8`
5. User receives `30e8` rsETH ≈ `3e-10` rsETH instead of `30e18` rsETH (30 rsETH).
6. User's 1 WBTC (~30 ETH) is permanently locked in the protocol.
7. `_getTotalEthInProtocol` adds `1e8 × 30e18 / 1e18 = 30e8` wei to TVL instead of `30e18` wei, understating TVL by `1e10`, inflating rsETH price for all existing holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/LRTOracle.sol (L336-348)
```text
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

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
