### Title
Stored `rsETHPrice` Overstated After Fee Minting Due to Missing Post-Mint Invariant Check — (`contracts/LRTOracle.sol`)

### Summary

`LRTOracle._updateRsETHPrice()` computes `newRsETHPrice` using the pre-fee-mint rsETH supply, then mints additional fee rsETH to the treasury, and finally stores `rsETHPrice = newRsETHPrice`. Because the supply increased after the price was computed, the stored price is permanently overstated until the next update cycle. No post-mint assertion verifies that `rsETHPrice` equals the actual price. This is the direct analog of the reported missing invariant check: a calculation produces a result that violates the protocol's core invariant (price = TVL / supply), and no guard catches it.

---

### Finding Description

In `_updateRsETHPrice()`, the price is computed at line 250 using the **pre-mint** supply:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [1](#0-0) 

Then fee rsETH is minted at line 306, increasing total supply:

```solidity
IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
``` [2](#0-1) 

Then the stored price is set to the pre-mint value:

```solidity
rsETHPrice = newRsETHPrice;
``` [3](#0-2) 

The actual price after minting is:

```
actualPrice = (totalETHInProtocol - protocolFeeInETH)
              / (rsethSupply + rsethAmountToMintAsProtocolFee)
            = (totalETHInProtocol - protocolFeeInETH)²
              / (rsethSupply × totalETHInProtocol)
```

which is strictly less than `newRsETHPrice` by a factor of `totalETHInProtocol / (totalETHInProtocol - protocolFeeInETH)`. There is no assertion of the form `rsETHPrice == actualPostMintPrice` anywhere in the function.

This stored price is then consumed by both withdrawal and deposit paths:

- **Withdrawals** (`getExpectedAssetAmount`): `underlyingToReceive = rsETHUnstaked * rsETHPrice / assetPrice` — overstated `rsETHPrice` yields more assets than owed. [4](#0-3) 

- **Deposits** (`getRsETHAmountToMint`): `rsethAmountToMint = amount * assetPrice / rsETHPrice` — overstated `rsETHPrice` yields fewer rsETH than owed. [5](#0-4) 

---

### Impact Explanation

Every call to `updateRSETHPrice()` that results in a non-zero `protocolFeeInETH` leaves `rsETHPrice` overstated for the entire window until the next update. During that window:

- A withdrawer calling `initiateWithdrawal()` or `instantWithdrawal()` receives `rsETHUnstaked × overstatement_factor` more underlying assets than their rsETH actually represents. This constitutes a small but real extraction of value from the pool at the expense of remaining holders and future depositors.
- A depositor receives fewer rsETH than the fair share of TVL they contributed.

The overstatement factor is `totalETHInProtocol / (totalETHInProtocol - protocolFeeInETH)`. For a 10 % protocol fee on 5 % APY rewards updated daily, this is approximately 1.000137 (0.014 % per update). The effect is small per cycle but is systematic and occurs on every reward-bearing update.

**Impact class**: Low — Contract fails to deliver promised returns (depositors) / small theft of unclaimed yield (withdrawers). [6](#0-5) 

---

### Likelihood Explanation

`updateRSETHPrice()` is a public, permissionless function callable by anyone when the contract is not paused. [7](#0-6)  It is expected to be called regularly (e.g., daily) by keepers. Every call where `totalETHInProtocol > previousTVL` and the protocol is not paused triggers fee minting and leaves the price overstated. The condition is routine during normal protocol operation with accruing EigenLayer rewards.

---

### Recommendation

After minting the fee rsETH, recompute `rsETHPrice` using the updated supply before storing it:

```solidity
// After minting fee rsETH, recompute price with updated supply
uint256 updatedSupply = IRSETH(rsETHTokenAddress).totalSupply();
rsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(updatedSupply);

// Invariant check (analog of the reported fix)
assert(rsETHPrice <= newRsETHPrice); // price must not be overstated
```

This ensures the stored `rsETHPrice` always equals `TVL / actualSupply` after the full update, matching the protocol's invariant. [8](#0-7) 

---

### Proof of Concept

Given:
- `totalETHInProtocol` = 1000 ETH
- `rsethSupply` = 950 (pre-mint)
- `protocolFeeInETH` = 1 ETH (10% fee on 10 ETH reward)

Step 1 — `newRsETHPrice = (1000 - 1) / 950 = 999/950 ≈ 1.05158 ETH/rsETH`

Step 2 — `rsethAmountToMintAsProtocolFee = 1 / 1.05158 ≈ 0.9509 rsETH` minted to treasury

Step 3 — `rsETHPrice` stored as `1.05158` (pre-mint value)

Actual post-mint price = `999 / (950 + 0.9509) = 999 / 950.9509 ≈ 1.05053 ETH/rsETH`

A withdrawer burning 100 rsETH immediately after the update receives:
- With stored price: `100 × 1.05158 / 1.0 = 105.158 ETH`
- With actual price: `100 × 1.05053 / 1.0 = 105.053 ETH`
- **Excess extracted: ~0.105 ETH** (0.01% of withdrawal)

This excess comes from diluting the remaining holders, exactly mirroring the invariant-decrease pattern in the reference report. [9](#0-8)

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

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
