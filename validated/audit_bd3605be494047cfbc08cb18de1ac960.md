### Title
Single Depositor Can Exhaust Global Daily Mint Limit, Temporarily Freezing All Deposits for Up to 24 Hours - (File: contracts/RSETH.sol)

---

### Summary

The `checkDailyMintLimit` modifier in `RSETH.sol` maintains a single global counter (`currentPeriodMintedAmount`) with no per-user allocation. Because `LRTDepositPool.depositETH` and `depositAsset` are permissionless, any depositor with sufficient capital can consume the entire daily mint quota in a single transaction, causing all subsequent deposit calls to revert with `DailyMintLimitExceeded` for the remainder of the 24-hour window.

---

### Finding Description

**Call path:**

```
LRTDepositPool.depositETH{value: X}(0, "")
  → _beforeDeposit(ETH_TOKEN, X, 0)          // no daily-limit check here
  → _mintRsETH(rsethAmountToMint)
      → IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)
          → checkDailyMintLimit(rsethAmountToMint)   // global counter updated
```

The `checkDailyMintLimit` modifier in `RSETH.sol`: [1](#0-0) 

It resets the period counter only when `block.timestamp >= periodStartTime + 1 days`, then checks and increments the **shared** `currentPeriodMintedAmount`. There is no per-depositor sub-limit, no minimum-remaining-quota reservation, and no mechanism to spread the daily budget across users.

`LRTDepositPool.depositETH` is fully permissionless: [2](#0-1) 

`_mintRsETH` delegates directly to `RSETH.mint`: [3](#0-2) 

**Exploit scenario:**

1. `maxMintAmountPerDay` is set to some finite value `M` (e.g., 1 000 ETH-equivalent in rsETH).
2. Attacker calls `depositETH{value: V}(0, "")` where `V` is chosen so that `rsethAmountToMint = M - 1` (just below the cap).
3. `currentPeriodMintedAmount` becomes `M - 1`.
4. Any subsequent depositor whose deposit would mint ≥ 2 wei of rsETH hits the revert: [4](#0-3) 

5. All deposits are blocked until `block.timestamp >= periodStartTime + 1 days`.

The attacker receives rsETH in return for their ETH, so the capital is not destroyed — it is merely locked in the protocol as rsETH until the attacker chooses to exit via the withdrawal path.

---

### Impact Explanation

All calls to `LRTDepositPool.depositETH` and `LRTDepositPool.depositAsset` that would mint any non-trivial rsETH amount revert for up to 24 hours. This constitutes **temporary freezing of funds** for every user who attempts to deposit during that window. The impact matches the Medium scope: *Temporary freezing of funds*.

---

### Likelihood Explanation

- No privileged role is required; any EOA or contract with sufficient ETH can trigger this.
- The attacker recovers their capital as rsETH (no permanent loss), making the attack economically viable as a griefing vector.
- The precondition (`maxMintAmountPerDay > 0`) is the intended production configuration — the manager sets it via `setMaxMintAmountPerDay`. [5](#0-4) 

---

### Recommendation

Introduce a per-transaction or per-user cap (e.g., no single deposit may consume more than X% of the daily limit), or reserve a minimum quota per block/slot so that no single depositor can exhaust the entire window. Alternatively, implement a proportional rate-limiting scheme that distributes the daily budget across multiple depositors rather than serving it first-come-first-served.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
// ... standard test setup imports

contract DailyMintExhaustTest is Test {
    // Assume: lrtDepositPool, rseth, lrtConfig, lrtOracle all deployed and configured
    // maxMintAmountPerDay = 1000 ether (in rsETH units)

    function testExhaustDailyLimit() public {
        address attacker  = makeAddr("attacker");
        address victim    = makeAddr("victim");

        // Fund attacker with enough ETH to mint ~maxMintAmountPerDay rsETH
        vm.deal(attacker, 1001 ether);
        vm.deal(victim,   1 ether);

        // Attacker deposits just below the daily cap
        vm.prank(attacker);
        lrtDepositPool.depositETH{value: 1000 ether}(0, "");

        // currentPeriodMintedAmount is now ≈ maxMintAmountPerDay

        // Victim's deposit reverts with DailyMintLimitExceeded
        vm.prank(victim);
        vm.expectRevert(
            abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector)
        );
        lrtDepositPool.depositETH{value: 1 ether}(0, "");

        // Confirm the block lasts until the next period
        vm.warp(block.timestamp + 23 hours);
        vm.prank(victim);
        vm.expectRevert(
            abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector)
        );
        lrtDepositPool.depositETH{value: 1 ether}(0, "");

        // After 24h the period resets and victim can deposit again
        vm.warp(block.timestamp + 1 hours + 1);
        vm.prank(victim);
        lrtDepositPool.depositETH{value: 1 ether}(0, ""); // succeeds
    }
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

**File:** contracts/RSETH.sol (L125-128)
```text
    function setMaxMintAmountPerDay(uint256 _maxMintAmountPerDay) external onlyLRTManager {
        maxMintAmountPerDay = _maxMintAmountPerDay;
        emit MaxMintAmountPerDayUpdated(_maxMintAmountPerDay);
    }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
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
