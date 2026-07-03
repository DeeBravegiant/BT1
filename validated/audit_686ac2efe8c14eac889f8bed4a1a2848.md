### Title
Incorrect Strict Inequality in Price-Decrease Circuit Breaker Allows Protocol to Remain Unpaused at Exact Threshold - (File: contracts/LRTOracle.sol)

### Summary
In `LRTOracle._updateRsETHPrice()`, the downside protection circuit breaker uses a strict `>` comparison instead of `>=` when checking whether the rsETH price drop exceeds the configured `pricePercentageLimit`. When the price drops by **exactly** the limit amount, `isPriceDecreaseOffLimit` evaluates to `false` and the protocol is not paused, contrary to the intended protection boundary.

### Finding Description
The `_updateRsETHPrice()` internal function implements a downside protection mechanism that is supposed to pause the protocol when the rsETH price falls too far from its all-time high (`highestRsethPrice`). The relevant check is:

```solidity
// downside protection — pause if price drops too far
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
    ...
}
```

The condition `diff > pricePercentageLimit.mulWad(highestRsethPrice)` is a strict inequality. When `diff == pricePercentageLimit.mulWad(highestRsethPrice)` — i.e., the price has dropped by **exactly** the configured limit — `isPriceDecreaseOffLimit` is `false`, and the protocol is **not** paused. The same off-by-one boundary defect exists in the symmetric upside check:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

When `priceDifference == pricePercentageLimit.mulWad(highestRsethPrice)`, non-manager callers are not blocked from updating the price to exactly the threshold value.

The entry path is fully public: `updateRSETHPrice()` is callable by any address with no access restriction. [1](#0-0) [2](#0-1) 

### Impact Explanation
When the rsETH price drops by exactly `pricePercentageLimit * highestRsethPrice / 1e18`, the circuit breaker silently fails to trigger. The `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` remain unpaused, allowing continued deposits and withdrawals at a price that has reached the maximum allowed drawdown. This is a **Low** severity issue: the contract fails to deliver its promised downside protection at the exact boundary value, but no direct fund theft or permanent freeze occurs. [3](#0-2) 

### Likelihood Explanation
Low. The equality `diff == pricePercentageLimit.mulWad(highestRsethPrice)` requires the computed price to land on a specific wei-level value. This is extremely unlikely to occur naturally. A deliberate attacker would need to manipulate the protocol's total ETH TVL (across all NodeDelegators, the DepositPool, the UnstakingVault, and the Converter) to produce a price that is exactly at the threshold — a practically infeasible level of precision. The `updateRSETHPrice()` function is public, so any caller can trigger the check, but controlling the output price to the exact wei is not realistic. [4](#0-3) 

### Recommendation
Change both strict `>` comparisons to `>=` so that a price movement **equal to** the configured limit is treated as out-of-bounds:

```solidity
// downside protection
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff >= pricePercentageLimit.mulWad(highestRsethPrice);

// upside protection
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference >= pricePercentageLimit.mulWad(highestRsethPrice);
``` [5](#0-4) [6](#0-5) 

### Proof of Concept

1. Admin sets `pricePercentageLimit = 1e16` (1%) and `highestRsethPrice = 1.05 ether`.
2. The threshold is `1e16 * 1.05e18 / 1e18 = 0.0105 ether`.
3. Protocol TVL shifts such that `newRsETHPrice = 1.05 ether − 0.0105 ether = 1.0395 ether`.
4. `diff = 0.0105 ether`.
5. `diff > 0.0105 ether` → `false` → `isPriceDecreaseOffLimit = false`.
6. `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` are **not** paused.
7. Any caller can invoke `updateRSETHPrice()` and the price is updated to `1.0395 ether` without triggering the circuit breaker, even though the price has dropped by exactly the configured limit. [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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
