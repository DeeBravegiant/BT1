Audit Report

## Title
stETH Priced via Chainlink Market Rate Feed Instead of Protocol Exchange Rate, Enabling Deposit/Withdrawal Arbitrage - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` is used for stETH with a Chainlink market rate feed that tracks secondary market prices, while every other supported LST (rETH, swETH, sfrxETH, EthX) uses a dedicated adapter calling a protocol-internal exchange rate. When stETH trades at a discount on secondary markets, an unprivileged attacker can call the public `updateRSETHPrice()` to depress the stored rsETH price, deposit ETH at the artificially low rsETH price to receive excess rsETH, then withdraw stETH — extracting value from existing rsETH holders' unclaimed yield.

## Finding Description

**Root cause — oracle type mismatch for stETH:**

`ChainlinkPriceOracle.getAssetPrice()` blindly delegates to whatever aggregator is configured per asset: [1](#0-0) 

The `contracts/oracles/` directory contains dedicated adapters for rETH, swETH, sfrxETH, and EthX — all calling protocol-internal exchange rate functions (`getExchangeRate()`, `getRate()`, `pricePerShare()`): [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

No `StETHPriceOracle` adapter exists. stETH is therefore priced via `ChainlinkPriceOracle` with the Chainlink stETH/ETH market rate feed, which tracks secondary market prices (e.g., Curve pool) and can depeg from Lido's internal exchange rate.

**TVL undervaluation and rsETH price depression:**

`_getTotalEthInProtocol()` multiplies each asset's balance by its oracle price: [6](#0-5) 

When stETH depegs, `getAssetPrice(stETH)` returns the depressed market rate, `totalETHInProtocol` is undervalued, and `rsETHPrice` drops. `updateRSETHPrice()` is public and callable by anyone: [7](#0-6) 

**Deposit at depressed price:**

`getRsETHAmountToMint` uses the stored `rsETHPrice`: [8](#0-7) 

When rsETH price is artificially depressed, the attacker receives more rsETH per ETH than fair value.

**Withdrawal at depressed prices:**

`getExpectedAssetAmount` computes stETH payout using the same depressed prices: [9](#0-8) 

**Why existing guards are insufficient:**

1. **`_calculatePayoutAmount` minimum check**: Takes `min(expectedAssetAmount, currentReturn)` at unlock time. This only reduces payout if prices drop *further* after initiation. If stETH recovers (the normal case), `currentReturn ≥ expectedAssetAmount` and the attacker receives the full inflated `expectedAssetAmount` locked in at depeg time. [10](#0-9) 

2. **`pricePercentageLimit` auto-pause**: The downside protection pauses the protocol only if the price drop exceeds `pricePercentageLimit`. If `pricePercentageLimit == 0` (disabled), there is no protection at all. If the depeg is within the limit (e.g., 0.5% depeg with a 1% limit), the protocol does not pause and the attack proceeds. [11](#0-10) 

3. **Instant withdrawal bypass**: If `isInstantWithdrawalEnabled[stETH]` is true, the 8-day delay is bypassed entirely, making the attack immediate: [12](#0-11) 

## Impact Explanation

**Impact: High — Theft of unclaimed yield.**

Existing rsETH holders suffer dilution of their accrued yield. The attacker deposits ETH at a time when rsETH is underpriced (due to stETH market depeg), receives excess rsETH, and withdraws stETH. When the market rate recovers, the attacker holds stETH worth more than the ETH they deposited. The excess rsETH minted to the attacker permanently dilutes all existing rsETH holders' share of the protocol's TVL, constituting theft of unclaimed yield. This matches the allowed impact scope exactly.

Concrete example (stETH market rate = 0.99 ETH, exchange rate = 1.0 ETH, 100% stETH backing):
- Protocol TVL: 100 stETH × 0.99 = 99 ETH; rsETH supply = 100; rsETH price = 0.99 ETH.
- Attacker deposits 1 ETH → mints `1 / 0.99 ≈ 1.0101 rsETH`.
- Attacker withdraws: `expectedAssetAmount = 1.0101 × 0.99 / 0.99 = 1.0101 stETH`.
- After recovery (stETH = 1.0 ETH): attacker holds 1.0101 stETH worth 1.0101 ETH — ~1% profit extracted from existing holders.

## Likelihood Explanation

**Likelihood: Medium.**

stETH market rate depegs are documented real-world events (e.g., April 13, 2024, ~1% depeg). The attack requires no privileged access: `updateRSETHPrice()` is public, `depositETH()` and `depositAsset()` are open to any user, and `initiateWithdrawal()` / `instantWithdrawal()` are open to any rsETH holder. The 8-day delay reduces but does not eliminate the risk; if instant withdrawal is enabled for stETH, the attack is immediate. The SECURITY.md exclusion for "depegging of an external stablecoin" does not apply — stETH is a liquid staking token, not a stablecoin, and the root cause is a code design flaw (market rate vs. exchange rate oracle selection), not merely an external market event.

## Recommendation

Replace the Chainlink stETH/ETH market rate feed with a protocol-internal exchange rate source. Create a dedicated `StETHPriceOracle` adapter (analogous to `RETHPriceOracle`, `SwETHPriceOracle`, etc.) that calls `stETH.getPooledEthByShares(1e18)` (Lido's protocol-guaranteed exchange rate). This rate is monotonically increasing and cannot be manipulated by secondary market sentiment, ensuring consistency with all other LST oracle adapters in the protocol.

## Proof of Concept

**Attack path (without instant withdrawal, 8-day delay, `pricePercentageLimit` within bounds):**

1. stETH depegs on secondary markets (Chainlink stETH/ETH feed drops to 0.99 ETH; Lido internal rate = 1.0 ETH). Depeg is within `pricePercentageLimit` so no auto-pause.
2. Attacker calls `LRTOracle.updateRSETHPrice()` — rsETH price drops to ~0.99 ETH.
3. Attacker calls `LRTDepositPool.depositETH{value: 1 ether}(0, "")` → receives `1e18 / 0.99e18 ≈ 1.0101e18` rsETH.
4. Attacker calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 1.0101e18, "")` → `expectedAssetAmount = 1.0101 stETH` locked in.
5. After 8 days, operator calls `unlockQueue(stETH, ...)`. stETH has recovered to 1.0 ETH; `currentReturn = 1.0101 × 1.0 / 1.0 = 1.0101 stETH ≥ expectedAssetAmount`, so payout = 1.0101 stETH.
6. Attacker calls `completeWithdrawal(stETH, "")` → receives 1.0101 stETH worth 1.0101 ETH — ~1% profit.

**Attack path (with instant withdrawal enabled):**

Steps 1–3 same as above. Then:

4. Attacker calls `LRTWithdrawalManager.instantWithdrawal(stETH, 1.0101e18, "")` → immediately receives 1.0101 stETH (minus instant withdrawal fee).
5. stETH market rate recovers; attacker holds 1.0101 stETH worth 1.0101 ETH — profit with no delay.

**Foundry fork test plan:** Fork mainnet at a block where stETH/ETH Chainlink feed shows a depeg. Deploy/configure the protocol with `ChainlinkPriceOracle` for stETH. Call `updateRSETHPrice()`, then `depositETH`, then `instantWithdrawal` (if enabled) or `initiateWithdrawal` + advance blocks + `unlockQueue` + `completeWithdrawal`. Assert attacker's final stETH balance × current exchange rate > initial ETH deposited.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L589-594)
```text
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
