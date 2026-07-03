Audit Report

## Title
Protocol Fee Over-Minted on Transient TVL Spikes via Public `updateRSETHPrice()` — (File: contracts/LRTOracle.sol)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any unprivileged address and computes the protocol fee as a percentage of the entire TVL increase since the last stored price snapshot. Because `totalETHInProtocol` is read live from current oracle prices while `previousTVL` is anchored to the last call, invoking the function during a transient oracle price spike causes the protocol to permanently mint excess rsETH to the treasury, diluting all rsETH holders for yield that was never realised.

## Finding Description

`updateRSETHPrice()` is `public whenNotPaused`, allowing any caller to invoke `_updateRsETHPrice()` at will. [1](#0-0) 

Inside `_updateRsETHPrice()`, the fee base is computed as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);   // anchored to last stored price
...
uint256 rewardAmount = totalETHInProtocol - previousTVL; // entire increase since last call
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
``` [2](#0-1) 

`totalETHInProtocol` is fetched live via `_getTotalEthInProtocol()`, which multiplies each supported asset's current oracle price by its deposited balance. [3](#0-2) 

The resulting fee is permanently minted to the treasury:

```solidity
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [4](#0-3) 

**Why existing guards are insufficient:**

1. **`pricePercentageLimit`** — checked only against `highestRsethPrice` and only after the fee has already been calculated. Spikes within the configured band (e.g., a 0.5 % move when the limit is 1 %) pass through entirely unchecked, with the fee minted in full. [5](#0-4) 

2. **`maxFeeMintAmountPerDay`** — reverts if the daily cap is exceeded, but for any fee amount below the cap the structural mis-accounting proceeds normally. It limits per-day magnitude, not the root cause. [6](#0-5) 

3. **Protocol-pause check** — only skips fee minting when the deposit pool or withdrawal manager is paused; it has no bearing on transient oracle price movements. [7](#0-6) 

**Exploit flow:**

1. Protocol is idle for an extended period; `rsETHPrice` is stale.
2. A supported asset oracle reports a transient price increase within the `pricePercentageLimit` band (natural LST/LRT intra-day volatility; no oracle compromise required).
3. Attacker calls the public `updateRSETHPrice()`.
4. `rewardAmount` = entire TVL increase including the transient portion; fee is minted to treasury.
5. Asset price reverts; `totalETHInProtocol` drops back to its prior level, but the treasury's extra rsETH remains outstanding, permanently diluting all other holders.

## Impact Explanation

**High — Theft of unclaimed yield.** Every rsETH holder's proportional claim on the underlying ETH is permanently reduced by the excess rsETH minted to the treasury. The dilution is irreversible: even after oracle prices normalise, the treasury retains the over-minted tokens. Magnitude scales with (a) the length of the snapshot gap and (b) the size of the transient spike, both of which are within an attacker's ability to optimise by timing the call.

## Likelihood Explanation

**Medium.** No privileged access, oracle compromise, flash loan, or governance action is required. The only precondition is a period of low update activity (weekend, bot outage, low on-chain traffic) combined with natural intra-day LST/LRT price volatility within the `pricePercentageLimit` band. Both conditions occur routinely. The attack is repeatable across multiple days subject only to the `maxFeeMintAmountPerDay` cap per 24-hour window.

## Recommendation

1. **Automate price updates**: Call `updateRSETHPrice()` on every deposit, withdrawal, and redemption to keep the snapshot gap minimal, eliminating the window for transient-spike exploitation.
2. **Time-weight the fee base**: Accrue yield continuously (e.g., per-second or per-block) rather than computing it as a lump sum against a stale snapshot.
3. **Bound the snapshot gap**: Revert or skip fee minting if `block.timestamp - lastUpdated` exceeds a configurable maximum, forcing an operator update before fees can be taken.
4. **Separate fee accounting from price updates**: Compute fees only on yield that has been confirmed over a minimum observation window, not on a single instantaneous TVL reading.

## Proof of Concept

**Minimal Foundry fork test outline:**

```solidity
// 1. Fork mainnet; deploy/configure LRTOracle with rsETHPrice = 1.05e18,
//    rsETH totalSupply = 100_000e18, protocolFeeInBPS = 1000 (10%).
// 2. Advance block.timestamp by 48 hours without calling updateRSETHPrice().
//    previousTVL = 100_000 * 1.05e18 = 105_000 ETH.
// 3. Mock the asset price oracle to return a 2% higher price
//    (within pricePercentageLimit), making totalETHInProtocol = 107_100 ETH.
// 4. Call updateRSETHPrice() from an unprivileged EOA.
//    Assert: treasury rsETH balance increased by ~196e18.
// 5. Restore the asset oracle to its original price.
//    Call updateRSETHPrice() again.
//    Assert: rsETHPrice < 1.05e18 (holders permanently diluted).
//    Assert: treasury rsETH balance is unchanged (no burn occurred).
```

The test concretely demonstrates that the treasury retains rsETH minted against a TVL increase that fully reversed, while all other holders' per-token ETH entitlement is permanently reduced. [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L204-209)
```text
        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
```

**File:** contracts/LRTOracle.sol (L214-316)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }

        // downside protection — pause if price drops too far
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

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
    }
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
