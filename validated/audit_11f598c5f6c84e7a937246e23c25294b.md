Audit Report

## Title
LST Price Oracles Use Only Internal Protocol Exchange Rates Without Market Price Validation, Enabling Deposit of Depegged Assets at Inflated Rates - (File: contracts/oracles/RETHPriceOracle.sol, contracts/oracles/SwETHPriceOracle.sol, contracts/oracles/SfrxETHPriceOracle.sol, contracts/oracles/EthXPriceOracle.sol)

## Summary

All four LST price oracles delegate entirely to a single on-chain source reflecting only the protocol's internal accounting rate, not the market price. If any supported LST depegs on the open market, the oracle continues to return the inflated internal rate, allowing any user to call the permissionless `LRTDepositPool.depositAsset()` and receive rsETH minted at a value exceeding the actual market value of the deposited asset. The existing downside-protection mechanism in `LRTOracle._updateRsETHPrice()` does not mitigate this attack because it also relies on the same inflated oracle prices.

## Finding Description

**Oracle code path (confirmed):**

`RETHPriceOracle.getAssetPrice()` returns only `IrETH(rETHAddress).getExchangeRate()` with no secondary market check. [1](#0-0) 

`SwETHPriceOracle.getAssetPrice()` returns only `ISwETH(swETHAddress).getRate()`. [2](#0-1) 

`SfrxETHPriceOracle.getAssetPrice()` returns only `ISfrxETH(sfrxETHContractAddress).pricePerShare()`. [3](#0-2) 

`EthXPriceOracle.getAssetPrice()` returns only `IETHXStakePoolsManager(...).getExchangeRate()`. [4](#0-3) 

**Deposit path (confirmed):**

`depositAsset()` is public and permissionless (guarded only by `whenNotPaused` and `onlySupportedERC20Token`). [5](#0-4) 

`getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`, routing directly to the single-source oracle above. [6](#0-5) 

**Why the existing downside-protection does not mitigate this:**

`LRTOracle._updateRsETHPrice()` contains a downside-protection mechanism that pauses the protocol if `newRsETHPrice` drops below `highestRsethPrice` by more than `pricePercentageLimit`. [7](#0-6) 

However, `_getTotalEthInProtocol()` computes total ETH value by calling `getAssetPrice(asset)` for each supported asset — the same inflated internal-rate oracle. [8](#0-7) 

Because the oracle still returns the inflated internal rate for the depegged LST, `totalETHInProtocol` is computed as if the deposited LST is worth its internal rate, not its market price. Consequently `newRsETHPrice` does not fall, the downside-protection threshold is never crossed, and the protocol is never paused by this mechanism. The protection is entirely blind to market-price depegs.

**Root cause:** The four LST-specific oracles have no secondary market price source. Internal protocol rates (accrued staking rewards) are monotonically increasing and do not reflect market depeg events. The `rsETHPrice` stored in `LRTOracle` is only updated when `updateRSETHPrice()` is called; between a depeg event and that call, the stored price is stale and inflated relative to the true backing.

## Impact Explanation

**Critical — Protocol insolvency.**

An attacker who purchases a depegged LST cheaply on the open market (e.g., rETH at 0.80 ETH due to a mass-slashing event or liquidity crisis) deposits it via `depositAsset()`. The oracle returns the internal rate (e.g., 1.07 ETH/rETH), so the attacker receives rsETH priced as if the deposit is worth 1.07 ETH per token. The attacker redeems or sells the rsETH, extracting value from the protocol. The protocol is left holding assets worth less than the rsETH liabilities outstanding — insolvency — causing direct, permanent losses to all existing rsETH holders. This matches the allowed impact "Protocol insolvency."

## Likelihood Explanation

LST depegs are not theoretical: stETH traded at ~0.94 ETH during the May 2022 market crisis without any protocol exploit — a pure liquidity/market event. Any of the four supported LSTs could depeg due to a mass-slashing event or liquidity crisis (neither of which constitutes an external protocol compromise). The attack requires no special permissions; any address can call `depositAsset()`. The attack is immediately profitable whenever the market price of a supported LST falls below its internal protocol rate, and can be repeated until the protocol is manually paused by the PAUSER_ROLE.

## Recommendation

Implement a dual-oracle check for each LST. For each asset, compare the internal protocol rate against a market price oracle (e.g., a Chainlink LST/ETH feed — `ChainlinkPriceOracle` already exists in the codebase). If the market price deviates below the internal rate by more than a configured threshold (e.g., 2%), either use the lower market price for minting calculations or revert new deposits of that asset. This prevents depeg exploitation while preserving normal operation when rates are in agreement. [9](#0-8) 

## Proof of Concept

**Preconditions:**
- rETH is a supported asset with `RETHPriceOracle` configured; `rETH.getExchangeRate()` returns `1.07e18`.
- rETH market price drops to `0.80 ETH` due to a mass-slashing event (no external protocol exploit required).
- Protocol is not paused; `rsETHPrice` is `1.07e18` (last stored value).

**Attack sequence:**
1. Attacker buys 1,000 rETH on the open market for 800 ETH.
2. Attacker calls `LRTDepositPool.depositAsset(rETH, 1000e18, minRSETH, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(rETH, 1000e18)`:
   - `lrtOracle.getAssetPrice(rETH)` → `RETHPriceOracle.getAssetPrice()` → `1.07e18`
   - `lrtOracle.rsETHPrice()` → `1.07e18`
   - `rsethAmountToMint = (1000e18 * 1.07e18) / 1.07e18 = 1000e18` rsETH (representing 1,070 ETH of claim at current rsETH price)
4. 1,000 rETH (market value: 800 ETH) is transferred in; 1,000 rsETH (claim value: 1,070 ETH) is minted.
5. Attacker sells rsETH, extracting ~270 ETH of value from existing holders.
6. Protocol holds 1,000 rETH worth 800 ETH against rsETH liabilities of 1,070 ETH — insolvent.
7. When `updateRSETHPrice()` is eventually called, `_getTotalEthInProtocol()` still uses the inflated oracle rate, so the downside-protection does not trigger.

**Foundry fork test plan:**
- Fork mainnet; deploy/configure the protocol with rETH and `RETHPriceOracle`.
- Mock `rETH.getExchangeRate()` to return `1.07e18` while setting rETH market price to `0.80e18` via a mock swap.
- Execute `depositAsset` as an unprivileged attacker with 1,000 rETH purchased at 0.80 ETH each.
- Assert rsETH minted represents more ETH value than the ETH spent to acquire the deposited rETH.
- Assert `updateRSETHPrice()` does not pause the protocol (downside protection blind to inflated oracle).

### Citations

**File:** contracts/oracles/RETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != rETHAddress) {
            revert InvalidAsset();
        }

        return IrETH(rETHAddress).getExchangeRate();
    }
```

**File:** contracts/oracles/SwETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != swETHAddress) {
            revert InvalidAsset();
        }

        return ISwETH(swETHAddress).getRate();
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

**File:** contracts/oracles/EthXPriceOracle.sol (L46-52)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != ethxAddress) {
            revert InvalidAsset();
        }

        return IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate();
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

**File:** contracts/LRTOracle.sol (L270-281)
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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L29-55)
```text
contract ChainlinkPriceOracle is IPriceFetcher, IAssetPriceFeed, LRTConfigRoleChecker, Initializable {
    mapping(address asset => address priceFeed) public override assetPriceFeed;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Initializes the contract
    /// @param lrtConfig_ LRT config address
    function initialize(address lrtConfig_) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfig_);

        lrtConfig = ILRTConfig(lrtConfig_);
        emit UpdatedLRTConfig(lrtConfig_);
    }

    /// @notice Fetches Asset/ETH exchange rate
    /// @param asset the asset for which exchange rate is required
    /// @return assetPrice exchange rate of asset
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```
