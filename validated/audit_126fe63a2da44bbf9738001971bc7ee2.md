### Title
Dust-Deposit Block Stuffing Exhausts Daily Mint Limit, Temporarily Locking Out Large Depositors - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

`deposit(string)` in `RSETHPoolV3` accepts any non-zero ETH amount and applies it against a shared `dailyMintLimit` with no minimum deposit floor. An attacker can submit many small deposits at elevated gas prices to simultaneously fill L2 blocks (blocking competing transactions) and exhaust `dailyMintAmount`, locking all other depositors out for the remainder of the day.

---

### Finding Description

The `deposit(string)` function applies the `limitDailyMint` modifier before executing the swap. The modifier computes the rsETH equivalent of the deposit and accumulates it into `dailyMintAmount`: [1](#0-0) 

The only guard against trivially small deposits is: [2](#0-1) 

There is no minimum deposit threshold beyond `amount != 0`. Inside `limitDailyMint`, the rsETH equivalent is computed and unconditionally added to `dailyMintAmount`: [3](#0-2) 

Once `dailyMintAmount` reaches `dailyMintLimit`, every subsequent deposit reverts: [4](#0-3) 

The daily counter only resets when `getCurrentDay() > lastMintDay`: [5](#0-4) 

An attacker submits N deposit transactions (each with a small but non-zero ETH value) at a gas price high enough to fill L2 blocks. Each transaction simultaneously:
1. Occupies block space, preventing competing legitimate deposits from being included.
2. Increments `dailyMintAmount` by the corresponding rsETH amount.

Because the attacker receives wrsETH for every deposit, the net cost is gas only — the deposited ETH is not lost. This makes the attack economically feasible compared to pure block stuffing where all gas is wasted.

---

### Impact Explanation

Once `dailyMintAmount == dailyMintLimit`, every call to `deposit()` reverts with `DailyMintLimitExceeded` until midnight UTC (relative to `startTimestamp`). Large depositors who could not get their transactions included during the stuffed blocks are locked out for the entire remaining day. This matches **Low — Block stuffing / temporary freezing of depositor access**.

---

### Likelihood Explanation

On L2 networks (the intended deployment environment, given the L2 bridge architecture), block gas limits are lower and base fees are cheaper than L1, making block stuffing significantly more affordable. The attacker recovers principal as wrsETH, so the net cost is only gas. No privileged role is required; any EOA with sufficient ETH for gas can execute this.

---

### Recommendation

1. **Enforce a minimum deposit amount**: Add a configurable `minDepositAmount` and revert if `msg.value < minDepositAmount`. This raises the cost per transaction and reduces the number of transactions needed to exhaust the limit.
2. **Per-address sub-limits or cooldowns**: Rate-limit individual depositors to prevent a single address from consuming a disproportionate share of the daily limit.
3. **Minimum rsETH output check inside `limitDailyMint`**: Revert if the computed `rsETHAmount == 0` (dust that rounds to zero) to prevent zero-value limit consumption.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fuzz/integration test (Foundry)
// Demonstrates that N small deposits summing to dailyMintLimit lock out a large depositor.

function testBlockStuffingExhaustsDailyLimit(
    RSETHPoolV3 pool,
    uint256 dailyMintLimit,   // e.g. 100 ether worth of rsETH
    uint256 dustAmount,       // e.g. 0.001 ether per tx
    uint256 N                 // number of attacker txs
) external {
    // Attacker sends N dust deposits, each incrementing dailyMintAmount
    for (uint256 i = 0; i < N; i++) {
        pool.deposit{value: dustAmount}("attacker");
    }

    // dailyMintAmount should now equal dailyMintLimit
    assertEq(pool.dailyMintAmount(), dailyMintLimit);

    // Legitimate large depositor is now locked out
    vm.expectRevert(RSETHPoolV3.DailyMintLimitExceeded.selector);
    pool.deposit{value: 10 ether}("victim");
}
```

The `limitDailyMint` modifier at [6](#0-5)  has no floor on `rsETHAmount`, so every non-zero deposit — no matter how small — contributes to exhausting the shared daily cap.

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L246-256)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();
```
