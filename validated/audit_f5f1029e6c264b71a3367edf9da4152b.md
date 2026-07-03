Looking at the two key contracts to trace the full exploit path.

### Title
Stale Cross-Chain Rate Enables Over-Issuance of rsETH Up to the Full Daily Mint Limit — (`contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` with no staleness guard. `RSETHPoolV3.deposit()` uses this rate to compute how many rsETH tokens to mint. Because the daily mint limit (`dailyMintLimit`) is denominated in **rsETH**, a stale (lower-than-true) rate allows an attacker to consume the entire daily limit while depositing less ETH than the minted rsETH is actually worth, extracting the accrued yield difference.

---

### Finding Description

`CrossChainRateReceiver` stores two fields: `rate` and `lastUpdated`. [1](#0-0) 

`lastUpdated` is written on every `lzReceive` call but is **never read** anywhere in the contract or by any consumer. `getRate()` returns `rate` unconditionally: [2](#0-1) 

`RSETHPoolV3.getRate()` delegates directly to this oracle: [3](#0-2) 

`viewSwapRsETHAmountAndFee` uses the rate to compute the rsETH amount: [4](#0-3) 

The `limitDailyMint` modifier computes `rsETHAmount` from the same stale rate and compares it against `dailyMintLimit` (which is in rsETH units): [5](#0-4) 

Because `dailyMintLimit` is in rsETH and the rsETH amount is computed with a stale low rate, the attacker can mint the full `dailyMintLimit` rsETH by depositing only `dailyMintLimit × stale_rate / 1e18` ETH — less than the `dailyMintLimit × true_rate / 1e18` ETH that the minted rsETH is actually worth.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Excess rsETH minted per day:

```
excess_rsETH = dailyMintLimit × (true_rate − stale_rate) / true_rate
```

The attacker receives rsETH redeemable for more ETH than they deposited. The shortfall is borne by the protocol (existing rsETH holders / treasury), representing the yield that accrued between the last rate update and the deposit. This repeats every day the rate remains stale.

---

### Likelihood Explanation

LayerZero message delivery is not guaranteed to be instantaneous. Network congestion, relayer downtime, or a missed `updateRate` call on the provider side can leave `rate` stale for hours or across a day boundary. rsETH accrues staking yield continuously, so `true_rate` drifts above `stale_rate` predictably. No special permissions are required — `deposit()` is a public, permissionless function. An attacker can monitor `lastUpdated` on-chain and act the moment a new day begins with a stale rate.

---

### Recommendation

1. **Add a staleness check in `getRate()`** (or in `RSETHPoolV3.getRate()`): revert or return a sentinel if `block.timestamp − lastUpdated > MAX_RATE_AGE` (e.g., 24 hours).
2. **Alternatively, denominate `dailyMintLimit` in ETH** rather than rsETH, so the cap bounds deposited value regardless of rate accuracy.
3. Consider emitting an alert or pausing deposits automatically when the rate has not been updated within the expected window.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Scenario (local fork or unit test):
// true_rate  = 1.05e18  (rsETH appreciated 5 % since last update)
// stale_rate = 1.00e18  (rate stored in CrossChainRateReceiver, not yet updated)
// dailyMintLimit = 100e18 rsETH
// feeBps = 0 (simplification)

// Step 1: A new day begins → dailyMintAmount resets to 0 inside limitDailyMint.
// Step 2: Attacker calls:
//   RSETHPoolV3.deposit{value: 100e18 * 1.00e18 / 1e18}("ref")
//   = deposit{value: 100 ETH}
// Step 3: viewSwapRsETHAmountAndFee(100e18):
//   rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100e18 rsETH   ← full daily limit consumed
// Step 4: At true_rate, 100 rsETH is worth:
//   100e18 * 1.05e18 / 1e18 = 105 ETH
// Step 5: Attacker deposited 100 ETH, received rsETH worth 105 ETH.
//   Profit = 5 ETH = dailyMintLimit * (true_rate - stale_rate) / true_rate
//          = 100e18 * 0.05e18 / 1.05e18 ≈ 4.76 ETH  (exact formula)

// Fuzz assertion:
// for stale_rate in [1e18, true_rate - 1]:
//   eth_deposited  = dailyMintLimit * stale_rate / 1e18
//   rsETH_received = dailyMintLimit          (hits the cap exactly)
//   eth_value_out  = dailyMintLimit * true_rate / 1e18
//   assert eth_value_out > eth_deposited     // always true when true_rate > stale_rate
```

The check `dailyMintAmount + rsETHAmount > dailyMintLimit` passes because `rsETHAmount` is computed with the stale rate and equals exactly `dailyMintLimit`. No guard prevents this on unmodified code. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-99)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```
