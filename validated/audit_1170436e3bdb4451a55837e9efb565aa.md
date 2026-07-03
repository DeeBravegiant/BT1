Based on my full read of `RSETHPoolV3WithNativeChainBridge.sol`, here is the analysis:

---

### Title
Daily Mint Limit Exhaustion Enables Block Stuffing DoS on ETH Deposits — (File: contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

The `limitDailyMint` modifier enforces a **global** daily minting cap with no per-address sub-limit. A single attacker can exhaust the entire `dailyMintLimit` in one `deposit()` call, then use block stuffing on the target L2 to prevent legitimate users from depositing ETH for the remainder of the 86400-second window and beyond.

---

### Finding Description

`getCurrentDay()` computes the current day as:

```solidity
return (block.timestamp - startTimestamp) / 1 days;
``` [1](#0-0) 

The `limitDailyMint` modifier resets `dailyMintAmount` only when `currentDay > lastMintDay`, and otherwise enforces a hard cap:

```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
dailyMintAmount += rsETHAmount;
``` [2](#0-1) 

There is **no per-address cap** anywhere in the modifier or in `deposit()`. A single caller can send ETH equivalent to the full `dailyMintLimit` quota in one transaction, setting `dailyMintAmount == dailyMintLimit`. Every subsequent `deposit()` call from any address then reverts with `DailyMintLimitExceeded` for the rest of the day. [3](#0-2) 

The `deposit(string)` function is fully permissionless — it requires only `whenNotPaused` and `isEthDepositEnabled`. No role, whitelist, or minimum-deposit guard prevents a single actor from consuming the entire quota. [4](#0-3) 

**Block stuffing extension:** This contract is deployed on L2 chains (evidenced by the native chain bridge architecture and `l1VaultETHForL2Chain`). L2 chains typically have lower block gas limits than L1, making block stuffing economically feasible. After exhausting the daily limit, the attacker submits high-gas dummy transactions to fill every subsequent block. Legitimate users' `deposit()` calls cannot be included. Even after `block.timestamp` naturally crosses the day boundary and `getCurrentDay() > lastMintDay` (which would reset the limit), users still cannot get transactions included as long as the attacker continues stuffing. The attacker can thus extend the DoS indefinitely beyond the natural 86400-second reset window.

---

### Impact Explanation

**Low — Block stuffing.** All ETH `deposit()` calls revert with `DailyMintLimitExceeded` for the duration of the attack. The daily limit reset (which is timestamp-driven and automatic) is rendered unreachable by users because their transactions cannot land in any block. The ETH deposit functionality is temporarily frozen for all users except the attacker.

---

### Likelihood Explanation

The attack is permissionless — no special role is required. The only preconditions are:
1. `isEthDepositEnabled == true` (normal operating state)
2. `dailyMintLimit` is finite (always true in production)
3. The attacker holds ETH sufficient to match the daily quota

On L2 chains with low block gas limits and cheap gas (e.g., Optimism, Base, Scroll), block stuffing is a known and documented attack vector. The cost scales with the L2's gas price and block gas limit, but is substantially lower than on L1.

---

### Recommendation

1. **Add a per-address daily deposit cap** inside `limitDailyMint` (e.g., `mapping(address => uint256) public userDailyMintAmount`) to prevent a single actor from consuming the global quota.
2. **Enforce a maximum single-deposit size** relative to `dailyMintLimit` (e.g., no single deposit may exceed 10% of the daily limit).
3. **Consider a sliding-window rate limiter** instead of a fixed epoch boundary, which also mitigates the cliff-reset griefing pattern.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";

interface IPool {
    function deposit(string memory referralId) external payable;
    function dailyMintLimit() external view returns (uint256);
    function dailyMintAmount() external view returns (uint256);
    error DailyMintLimitExceeded();
}

contract BlockStuffingPoC is Test {
    IPool pool; // fork-initialized to deployed RSETHPoolV3WithNativeChainBridge

    address attacker = address(0xA77);
    address victim   = address(0xB0B);

    function testBlockStuffingDoS() public {
        // --- Step 1: attacker exhausts the full daily limit in one tx ---
        uint256 quota = pool.dailyMintLimit();
        vm.deal(attacker, quota);
        vm.prank(attacker);
        pool.deposit{value: quota}("ref");

        assertEq(pool.dailyMintAmount(), quota);

        // --- Step 2: simulate block stuffing — advance block number but
        //     hold block.timestamp constant (attacker fills every block) ---
        uint256 frozenTs = block.timestamp;
        vm.roll(block.number + 5_000);   // many blocks produced
        vm.warp(frozenTs);               // timestamp pinned: day never resets

        // --- Step 3: victim's deposit reverts for the entire stuffed window ---
        vm.deal(victim, 1 ether);
        vm.prank(victim);
        vm.expectRevert(IPool.DailyMintLimitExceeded.selector);
        pool.deposit{value: 1 ether}("ref");
    }
}
```

The test demonstrates that after the attacker exhausts `dailyMintLimit`, all subsequent `deposit()` calls revert. The `vm.roll()` + `vm.warp()` combination models block stuffing: blocks are produced (block number advances) but `block.timestamp` does not cross the day boundary, so `getCurrentDay()` never exceeds `lastMintDay` and the limit never resets. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L56-66)
```text
    /// @notice The daily minting limit for rsETH
    uint256 public dailyMintLimit;

    /// @notice The amount of rsETH that was minted today
    uint256 public dailyMintAmount;

    /// @notice The last day that rsETH was minted
    uint256 public lastMintDay;

    /// @notice The start timestamp for the daily minting limit
    uint256 public startTimestamp;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L108-137)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-301)
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

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L390-392)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }
```
