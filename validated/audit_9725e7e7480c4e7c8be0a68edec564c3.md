### Title
Unbounded `pricePercentageLimit` in `LRTOracle.setPricePercentageLimit` Causes Permanent Overflow-Revert in Price Update Path - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.setPricePercentageLimit` accepts any `uint256` value with no upper-bound check. Once `highestRsethPrice` grows above `1e18` (guaranteed as rsETH accrues staking yield), setting `pricePercentageLimit` to a sufficiently large value causes `pricePercentageLimit.mulWad(highestRsethPrice)` to overflow inside `_updateRsETHPrice()`. Every subsequent call to the public `updateRSETHPrice()` permanently reverts, freezing the rsETH price and breaking fee minting for all users.

---

### Finding Description

`setPricePercentageLimit` accepts an unbounded `uint256` with no validation:

```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;          // no upper-bound check
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
``` [1](#0-0) 

The stored value is consumed in `_updateRsETHPrice()` at two points:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
``` [2](#0-1) 

```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
``` [3](#0-2) 

`mulWad` delegates to OpenZeppelin's `Math.mulDiv(pricePercentageLimit, highestRsethPrice, 1e18)`:

```solidity
function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
    z = x.mulDiv(y, WAD);
}
``` [4](#0-3) 

While `mulDiv` uses 512-bit intermediate arithmetic to avoid intermediate overflow, it **reverts** when the final quotient exceeds `type(uint256).max`. The overflow condition is:

```
pricePercentageLimit * highestRsethPrice / 1e18 > type(uint256).max
⟺ pricePercentageLimit > type(uint256).max × 1e18 / highestRsethPrice
```

Since rsETH is a yield-bearing token, `highestRsethPrice` grows above `1e18` over time (e.g., `1.1e18` after ~10% yield). At that point, setting `pricePercentageLimit = type(uint256).max` causes `mulDiv` to revert on every call to `_updateRsETHPrice()`, because `type(uint256).max × 1.1e18 / 1e18 = type(uint256).max × 1.1` overflows `uint256`.

The overflow is hit on every price update because the price either increases (line 257 branch) or decreases (line 274 branch) relative to `highestRsethPrice`. The only escape is if the price is exactly equal to `highestRsethPrice`, which is a transient condition.

`_updateRsETHPrice()` is the sole path for:
1. Updating `rsETHPrice` (used by `LRTDepositPool.getRsETHAmountToMint` for all deposits)
2. Minting protocol fee rsETH to the treasury
3. Triggering the downside-protection auto-pause [5](#0-4) 

---

### Impact Explanation

**Permanent freezing of unclaimed yield (Medium):** Fee minting via `_updateRsETHPrice()` is permanently broken. The protocol treasury never receives its share of staking rewards after the overflow is triggered.

**Contract fails to deliver promised returns (Low):** `rsETHPrice` is frozen at the last value. Depositors' rsETH tokens no longer reflect the actual value of the underlying assets. The downside-protection auto-pause (lines 277–281) is also permanently disabled, meaning the protocol cannot auto-pause if underlying assets lose value (e.g., slashing), allowing withdrawals at a stale inflated price. [6](#0-5) 

---

### Likelihood Explanation

The LRT admin role is required to call `setPricePercentageLimit`. The protocol's own NatSpec documents the intended range as `1e16` (1%) to `1e18` (100%), but this range is never enforced on-chain. An admin setting the value in the wrong unit (e.g., `1e36` instead of `1e18` for "100%") or passing `type(uint256).max` to "disable" the limit would silently arm the overflow. The trigger condition (`highestRsethPrice > 1e18`) is guaranteed to be met as rsETH accrues yield over time. [7](#0-6) 

---

### Recommendation

Add an upper-bound check matching the documented semantic (100% = `1e18`):

```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit > 1e18) revert InvalidPricePercentageLimit();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

---

### Proof of Concept

1. rsETH accrues yield; `highestRsethPrice` grows to `1.1e18`.
2. LRT admin calls `setPricePercentageLimit(type(uint256).max)` — no revert, no validation.
3. Any user calls `updateRSETHPrice()`.
4. `_updateRsETHPrice()` computes `newRsETHPrice`. Suppose it is `> highestRsethPrice` (price increased).
5. Execution reaches line 257: `pricePercentageLimit.mulWad(highestRsethPrice)` = `mulDiv(type(uint256).max, 1.1e18, 1e18)`.
6. The quotient `type(uint256).max × 1.1` overflows `uint256`; `mulDiv` reverts.
7. `updateRSETHPrice()` reverts. Every future call reverts identically.
8. `rsETHPrice` is permanently frozen; fee minting is permanently broken; downside-protection auto-pause is permanently disabled. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L121-128)
```text
    /// @dev set the price percentage limit. Only onlyLRTAdmin is allowed
    /// @dev PricePercentageLimit for 1% is 1e16
    /// @dev Price Percentage Limit for 100% is 1e18
    /// @param _pricePercentageLimit price percentage limit
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
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

**File:** contracts/utils/WadMath.sol (L17-19)
```text
    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(y, WAD);
    }
```
