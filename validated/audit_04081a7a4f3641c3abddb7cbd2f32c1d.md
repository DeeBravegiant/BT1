Audit Report

## Title
Dust-Deposit Attack Exhausts Daily Mint Limit, Temporarily Locking Out Depositors - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `deposit(string)` function in `RSETHPoolV3` applies the `limitDailyMint` modifier with no minimum deposit floor beyond `amount != 0`. An attacker can submit many small-value deposits to exhaust `dailyMintAmount` up to `dailyMintLimit`, causing all subsequent deposits to revert with `DailyMintLimitExceeded` for the remainder of the day. Because the attacker receives wrsETH for each deposit, the net cost is gas only, making the attack economically feasible — especially on L2 where gas is cheap.

## Finding Description
The `limitDailyMint` modifier (L96–125) computes `rsETHAmount` via `viewSwapRsETHAmountAndFee` and unconditionally accumulates it into `dailyMintAmount` before the function body executes:

```solidity
// L123
dailyMintAmount += rsETHAmount;
```

The only guard against zero-value deposits is in the function body at L256 (`if (amount == 0) revert InvalidAmount()`), which executes *after* the modifier has already updated `dailyMintAmount`. Any non-zero ETH deposit — no matter how small — contributes to the shared daily cap.

Once the cap is reached (L119–121):
```solidity
if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
    revert DailyMintLimitExceeded();
}
```

All deposits revert until `getCurrentDay() > lastMintDay` (L113–116), which resets only at the next UTC-relative day boundary computed as `(block.timestamp - startTimestamp) / 1 days` (L340).

The attacker's exploit path:
1. Submit N deposits with small but non-zero `msg.value` (e.g., 0.001 ETH each), optionally at elevated gas price to also fill L2 blocks.
2. Each transaction increments `dailyMintAmount` by the corresponding rsETH equivalent.
3. Once `dailyMintAmount == dailyMintLimit`, every subsequent `deposit()` call reverts.
4. The attacker receives wrsETH for every deposit, recovering principal minus gas.

Existing checks are insufficient: `nonReentrant` prevents reentrancy but not repeated external calls; `whenNotPaused` requires admin intervention; the `amount == 0` check in the function body runs after the modifier has already consumed limit.

## Impact Explanation
Once `dailyMintAmount` reaches `dailyMintLimit`, all depositors are locked out until the next day boundary. This is a concrete, temporary freezing of depositor access matching **Low — Block stuffing** from the allowed impact scope. The attacker does not lose funds (wrsETH is received), so the attack is repeatable every day at gas cost only.

## Likelihood Explanation
No privileged role is required — any EOA with ETH for gas can execute this. On L2 networks (the intended deployment given the L2 bridge architecture), block gas limits are lower and base fees are significantly cheaper than L1, making both block stuffing and repeated small deposits economically viable. The attacker recovers principal as wrsETH, so the sustained daily cost is only gas. The attack is repeatable every 24-hour window.

## Recommendation
1. **Enforce a minimum deposit amount**: Add a configurable `minDepositAmount` state variable and revert in `limitDailyMint` (or at the top of `deposit`) if `msg.value < minDepositAmount`. This raises the per-transaction cost and reduces the number of transactions needed to exhaust the limit.
2. **Revert on zero rsETH output inside `limitDailyMint`**: Add `if (rsETHAmount == 0) revert InvalidAmount();` inside the modifier to prevent dust that rounds to zero from consuming limit (and to catch extreme dust cases).
3. **Per-address sub-limits or cooldowns**: Rate-limit individual depositors to prevent a single address from consuming a disproportionate share of the daily limit.

## Proof of Concept
```solidity
// Foundry integration test
function testDustExhaustsDailyLimit() external {
    // Setup: dailyMintLimit = 100 ether worth of rsETH, dustAmount = 0.001 ether
    uint256 dustAmount = 0.001 ether;
    // N deposits needed to exhaust limit (N = dailyMintLimit / rsETHPerDust)
    uint256 N = /* dailyMintLimit / viewSwapRsETHAmountAndFee(dustAmount).rsETHAmount */ 100_000;

    vm.startPrank(attacker);
    for (uint256 i = 0; i < N; i++) {
        pool.deposit{value: dustAmount}("attacker");
    }
    vm.stopPrank();

    // dailyMintAmount now equals dailyMintLimit
    assertEq(pool.dailyMintAmount(), pool.dailyMintLimit());

    // Legitimate depositor is locked out for the rest of the day
    vm.prank(victim);
    vm.expectRevert(RSETHPoolV3.DailyMintLimitExceeded.selector);
    pool.deposit{value: 10 ether}("victim");
}
```

The `limitDailyMint` modifier at L96–125 has no floor on `rsETHAmount`, so every non-zero deposit contributes to exhausting the shared daily cap. The daily reset at L113–116 only occurs when `getCurrentDay() > lastMintDay`, meaning the lockout persists until the next day boundary.