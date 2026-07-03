### Title
Unbounded `setMaxFeeMintAmountPerDay` Allows Removal of Daily Fee Minting Safety Cap, Enabling Uncapped rsETH Dilution of Holders - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.setMaxFeeMintAmountPerDay` accepts any `uint256` value with no upper bound, directly mirroring the external report's root cause. A manager can set it to `type(uint256).max`, rendering the daily fee minting guard in `_checkAndUpdateDailyFeeMintLimit` permanently inert and allowing the full `protocolFeeInBPS`-derived fee to be minted on every `updateRSETHPrice` call without any daily throttle.

### Finding Description
`setMaxFeeMintAmountPerDay` in `LRTOracle.sol` sets `maxFeeMintAmountPerDay` to whatever value is passed, with no ceiling check:

```solidity
function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
    maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
    emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
}
``` [1](#0-0) 

The guard that enforces the cap is `_checkAndUpdateDailyFeeMintLimit`:

```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
``` [2](#0-1) 

When `maxFeeMintAmountPerDay == type(uint256).max`, the condition `currentPeriodMintedFeeAmount + feeAmount > type(uint256).max` can never be true (Solidity 0.8 would revert on overflow before reaching the comparison, but `feeAmount` is bounded by actual TVL growth so overflow never occurs in practice). The guard is therefore permanently bypassed.

The fee minted per `updateRSETHPrice` call is:

```solidity
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [3](#0-2) 

`protocolFeeInBPS` is capped at 1500 BPS (15%) by `LRTConfig.setProtocolFeeBps`: [4](#0-3) 

With the daily cap removed, the manager can call `updateRSETHPriceAsManager` (or allow `updateRSETHPrice` to be called publicly) on every block during a period of high TVL growth, minting up to 15% of each incremental TVL gain as rsETH to the treasury on each call, with no daily throttle to limit aggregate dilution.

By contrast, `setProtocolFeeBps` in `LRTConfig.sol` correctly enforces `if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit()`, demonstrating that the codebase already applies the pattern of upper-bound enforcement for fee parameters — but omits it for `maxFeeMintAmountPerDay`.

### Impact Explanation
**High — Theft of unclaimed yield.**

rsETH holders' yield accrues as TVL growth reflected in the rsETH price. The daily fee minting cap is the sole rate-limiting mechanism preventing the treasury from extracting an unbounded share of that yield per day. With the cap set to `type(uint256).max`, the manager can drain the full 15% fee on every TVL increment without any daily throttle, permanently redirecting yield that would otherwise accrue to rsETH holders to the protocol treasury.

### Likelihood Explanation
**Low.** Exploitation requires the `LRTManager` role to call `setMaxFeeMintAmountPerDay(type(uint256).max)`. The role is privileged, but the absence of any on-chain upper bound means there is no code-level safeguard preventing this — identical to the external report's root cause where `adminChangeFee` had no ceiling.

### Recommendation
Add an explicit upper bound to `setMaxFeeMintAmountPerDay`, analogous to the fix applied in the referenced Chiliz commit. For example:

```solidity
uint256 public constant MAX_FEE_MINT_PER_DAY = <protocol-defined ceiling>;

function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
    if (_maxFeeMintAmountPerDay > MAX_FEE_MINT_PER_DAY) revert ExceedsMaxFeeMintPerDay();
    maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
    emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
}
```

This mirrors the pattern already used in `setProtocolFeeBps` and in `KernelMerkleDistributor.setFeeInBPS` / `KernelTop100MerkleDistributor.setFeeInBPS`, both of which enforce `if (_feeInBPS > MAX_FEE_IN_BPS) revert InvalidFeeInBPS()`. [5](#0-4) 

### Proof of Concept
1. Manager calls `setMaxFeeMintAmountPerDay(type(uint256).max)`.
2. TVL grows (e.g., staking rewards accrue). `protocolFeeInBPS` is at 1500 BPS.
3. Anyone calls `updateRSETHPrice()` (public, no access control).
4. `_updateRsETHPrice` computes `protocolFeeInETH = rewardAmount * 1500 / 10_000`.
5. `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` evaluates `currentPeriodMintedFeeAmount + feeAmount > type(uint256).max` — always false.
6. rsETH is minted to treasury with no daily cap enforced.
7. Step 3–6 can repeat every time TVL increments, with no daily throttle preventing aggregate extraction. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L130-135)
```text
    /// @dev set the maximum fee minting amount per day. Only onlyLRTManager is allowed
    /// @param _maxFeeMintAmountPerDay maximum amount of fee that can be minted per day
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
```

**File:** contracts/LRTOracle.sol (L244-307)
```text
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
```

**File:** contracts/LRTConfig.sol (L196-199)
```text
    function setProtocolFeeBps(uint256 _protocolFeeInBPS) external onlyRole(LRTConstants.MANAGER) {
        if (_protocolFeeInBPS > 1500) revert ProtocolFeeExceedsLimit();
        protocolFeeInBPS = _protocolFeeInBPS;
        emit UpdateFee(_protocolFeeInBPS);
```

**File:** contracts/KERNEL/KernelMerkleDistributor.sol (L388-390)
```text
    function setFeeInBPS(uint256 _feeInBPS) external onlyOwner {
        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
```
