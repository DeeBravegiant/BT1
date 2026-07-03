The vulnerability claim requires careful tracing of two separate daily-limit systems in the codebase.

### Title
Shared `RSETH.checkDailyMintLimit` Cap Allows Depositor to Block Protocol Fee Minting — (`contracts/RSETH.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`RSETH.mint()` applies the `checkDailyMintLimit` modifier to every caller, including the `LRTOracle` when it mints protocol fees. An unprivileged depositor who exhausts `RSETH.maxMintAmountPerDay` within a period causes every subsequent `updateRSETHPrice()` call to revert, freezing protocol fee yield for the entire period. The attack is repeatable every 24 hours at the cost of gas only.

---

### Finding Description

There are **two independent daily-limit systems** in the protocol:

**System 1 — `RSETH.sol` (applies to all `RSETH.mint()` calls):** [1](#0-0) 

`currentPeriodMintedAmount` and `maxMintAmountPerDay` are shared by every caller of `RSETH.mint()`, regardless of whether the caller is a depositor or the oracle.

**System 2 — `LRTOracle.sol` (fee-specific limit, independent state):** [2](#0-1) 

This is a separate cap (`maxFeeMintAmountPerDay` / `currentPeriodMintedFeeAmount`) that only governs the oracle's own accounting.

**The fee mint path in `_updateRsETHPrice()`:** [3](#0-2) 

Line 303 passes the oracle's own fee-limit check. Line 306 then calls `IRSETH.mint(treasury, rsethAmountToMintAsProtocolFee)`, which re-enters `RSETH.mint()`: [4](#0-3) 

The `checkDailyMintLimit` modifier on `RSETH.mint()` checks `RSETH.currentPeriodMintedAmount` — the **same counter** that user deposits increment. If a depositor has already filled `maxMintAmountPerDay`, this reverts with `DailyMintLimitExceeded`, causing the entire `updateRSETHPrice()` transaction to revert. `rsETHPrice` is never updated and the fee is never minted for that period.

**User deposit path for reference:** [5](#0-4) 

Both paths converge on the same `RSETH.mint()` → `checkDailyMintLimit` gate with no priority ordering between depositor mints and fee mints.

---

### Impact Explanation

When `maxMintAmountPerDay` is set to a finite value and a depositor fills the cap:

1. `updateRSETHPrice()` reverts — `rsETHPrice` is not updated (stale price).
2. Protocol fee rsETH is not minted to the treasury for that period.
3. Because `rsETHPrice` is stale, the next successful `updateRSETHPrice()` call will recalculate the fee using the old price, so the skipped fee is partially recovered in the next period — but only if the attacker does not repeat the block.
4. If the attacker repeats the fill every period (cheap: they receive rsETH for their deposit and only pay gas), the treasury fee is perpetually blocked. The yield accrues in the TVL but is never extracted as rsETH to the treasury — **permanent freezing of unclaimed protocol yield**.

Impact: **Medium — Permanent freezing of unclaimed yield** (repeatable every period).

---

### Likelihood Explanation

- Requires `maxMintAmountPerDay` to be set to a finite non-zero value by the manager — a valid and intended operational state.
- Requires the attacker to deposit enough ETH/LST to fill the remaining daily cap — no special role needed, only capital (which is returned as rsETH).
- The attack costs only gas per period; the depositor suffers no capital loss.
- `updateRSETHPrice()` is a public function callable by anyone, so the attacker can also time the fill to occur just before a price update.

Likelihood: **Medium**.

---

### Recommendation

Decouple protocol fee minting from the user-deposit daily cap. Two concrete options:

1. **Bypass `checkDailyMintLimit` for fee mints**: Add a separate internal `_mintFee(address to, uint256 amount)` function in `RSETH` that holds the `MINTER_ROLE` check and `whenNotPaused` but skips `checkDailyMintLimit`, relying solely on `LRTOracle._checkAndUpdateDailyFeeMintLimit` for fee-specific rate limiting.

2. **Reserve headroom for fees**: Before allowing a user deposit, verify that `currentPeriodMintedAmount + depositAmount + expectedMaxFee <= maxMintAmountPerDay`, where `expectedMaxFee` is a configured buffer. This is more complex and error-prone.

Option 1 is simpler and cleanly separates the two concerns.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Preconditions (set by manager before test):
//   rsETH.maxMintAmountPerDay = 100 ether
//   lrtOracle.maxFeeMintAmountPerDay = 10 ether
//   Protocol has accrued rewards (totalETHInProtocol > previousTVL)

function testFeeBlockedByDepositCap() public {
    // 1. Attacker deposits enough to fill the RSETH daily mint cap
    uint256 remaining = rsETH.remainingDailyMintLimit(); // e.g. 100 ether
    vm.deal(attacker, remaining * 2); // fund attacker
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: remaining}(0, "");
    // currentPeriodMintedAmount == maxMintAmountPerDay now

    // 2. Anyone calls updateRSETHPrice() — reverts
    vm.expectRevert(
        abi.encodeWithSelector(
            RSETH.DailyMintLimitExceeded.selector,
            rsETH.maxMintAmountPerDay() + feeAmount,
            rsETH.maxMintAmountPerDay()
        )
    );
    lrtOracle.updateRSETHPrice();

    // 3. rsETHPrice is stale; treasury received no fee rsETH
    assertEq(rsETH.balanceOf(treasury), 0);

    // 4. Repeat next period — attacker fills cap again, fee blocked again
    vm.warp(block.timestamp + 1 days + 1);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: remaining}(0, "");
    vm.expectRevert();
    lrtOracle.updateRSETHPrice();
}
```

### Citations

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
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

**File:** contracts/LRTOracle.sol (L298-311)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
