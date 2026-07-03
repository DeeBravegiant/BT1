### Title
Whitelisted User Can Inflate `whitelistedUnstakeAllowance` Without Actual Withdrawal Obligations, Enabling Operator to Over-Unstake stETH - (`contracts/LRTConverter.sol`)

---

### Summary

`declareWithdrawalIntent` imposes no per-user cap, no verification against actual pending withdrawal queue entries, and no cooldown. A whitelisted user can declare up to `1_000_000_000 ether` of allowance in a single call, inflating the global `whitelistedUnstakeAllowance` far beyond the protocol's real pending stETH obligations. The operator can then call `unstakeStEth` against the full converter stETH balance, locking it in Lido's withdrawal queue for days.

---

### Finding Description

`declareWithdrawalIntent` in `contracts/LRTConverter.sol` only checks that the new total does not exceed the hard cap:

```solidity
// LRTConverter.sol L216-226
function declareWithdrawalIntent(uint256 amount) external nonReentrant onlyWhitelistedUser {
    if (amount == 0) { revert InvalidAmount(); }
    uint256 maxWhitelistedAllowance = 1_000_000_000 ether;
    if (whitelistedUnstakeAllowance + amount > maxWhitelistedAllowance) {
        revert WhitelistedAllowanceExceeded();
    }
    whitelistedUnstakeAllowance = whitelistedUnstakeAllowance + amount;
    emit WithdrawalIntentDeclared(msg.sender, amount);
}
``` [1](#0-0) 

There is no check that:
- The caller has any rsETH burn request or withdrawal queue entry in `LRTWithdrawalManager`
- The declared amount is bounded by the caller's rsETH balance or any per-user limit
- The allowance was previously consumed before it can be re-declared

The `withinUnstakeLimits` modifier then permits `unstakeStEth` to proceed whenever `amountToUnstake <= whitelistedUnstakeAllowance + availableActiveETHWithdrawals`:

```solidity
// LRTConverter.sol L65-74
if (amountToUnstake > whitelistedUnstakeAllowance + availableActiveETHWithdrawals) {
    revert UnstakeLimitExceeded();
}
if (whitelistedUnstakeAllowance > 0) {
    uint256 whitelistedAmountConsumed = ...;
    whitelistedUnstakeAllowance -= whitelistedAmountConsumed;
}
``` [2](#0-1) 

The operator's `unstakeStEth` call then submits the full amount to Lido's withdrawal queue:

```solidity
// UnstakeStETH.sol L48-56
function _unstakeStEth(uint256 amountToUnstake) internal {
    stETH.safeIncreaseAllowance(address(withdrawalQueue), amountToUnstake);
    ...
    uint256[] memory requestIds = withdrawalQueue.requestWithdrawals(amounts, address(this));
}
``` [3](#0-2) 

---

### Impact Explanation

The operator can unstake the converter's entire stETH balance into Lido's withdrawal queue based on a fictitious allowance. stETH is not lost but is illiquid for the multi-day Lido finalization period. During this window the protocol cannot service LST withdrawal requests that depend on stETH liquidity, causing it to fail to deliver promised returns without losing value. This matches the scoped impact: **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

Likelihood is **low-medium**. It requires:
1. The manager to whitelist a user (normal operational action, not collusion — the user may have been whitelisted legitimately).
2. That whitelisted user to declare an amount far exceeding their actual withdrawal obligations.
3. The operator to call `unstakeStEth` against the inflated allowance (the operator acts in good faith, seeing a large allowance and a large stETH balance).

No private key compromise or explicit collusion is required; a legitimately whitelisted user acting in bad faith is sufficient.

---

### Recommendation

1. **Bind declared intent to actual withdrawal queue entries**: verify that the caller has at least `amount` worth of pending ETH withdrawals in `LRTWithdrawalManager` (`assetsCommitted[ETH_TOKEN]` attributed to the caller) before incrementing `whitelistedUnstakeAllowance`.
2. **Track per-user declared amounts**: maintain a `mapping(address => uint256) public declaredWithdrawalIntent` and cap each user's contribution to their verifiable pending obligations.
3. **Provide a cancellation path**: allow the manager or the user to reduce `whitelistedUnstakeAllowance` if the intent is no longer valid.

---

### Proof of Concept

```solidity
// Local fork test (no mainnet interaction)
function test_inflateAllowanceAndOverUnstake() public {
    // 1. Manager whitelists attacker
    vm.prank(manager);
    lrtConverter.setUserWhitelisted(attacker, true);

    // 2. Attacker declares near-max allowance with no actual withdrawal
    vm.prank(attacker);
    lrtConverter.declareWithdrawalIntent(999_999_999 ether);

    // 3. whitelistedUnstakeAllowance is now 999_999_999 ether
    assertEq(lrtConverter.whitelistedUnstakeAllowance(), 999_999_999 ether);

    // 4. Operator unstakes the converter's entire stETH balance
    uint256 stEthBalance = stETH.balanceOf(address(lrtConverter));
    vm.prank(operator);
    lrtConverter.unstakeStEth(stEthBalance); // passes withinUnstakeLimits

    // 5. stETH is now locked in Lido queue; converter stETH balance = 0
    assertEq(stETH.balanceOf(address(lrtConverter)), 0);
    // Protocol cannot service stETH-dependent withdrawals until Lido finalizes (days)
}
```

### Citations

**File:** contracts/LRTConverter.sol (L65-74)
```text
        if (amountToUnstake > whitelistedUnstakeAllowance + availableActiveETHWithdrawals) {
            revert UnstakeLimitExceeded();
        }

        // Consume intended withdrawal limit
        if (whitelistedUnstakeAllowance > 0) {
            uint256 whitelistedAmountConsumed =
                amountToUnstake > whitelistedUnstakeAllowance ? whitelistedUnstakeAllowance : amountToUnstake;

            whitelistedUnstakeAllowance -= whitelistedAmountConsumed;
```

**File:** contracts/LRTConverter.sol (L216-226)
```text
    function declareWithdrawalIntent(uint256 amount) external nonReentrant onlyWhitelistedUser {
        if (amount == 0) {
            revert InvalidAmount();
        }
        uint256 maxWhitelistedAllowance = 1_000_000_000 ether;
        if (whitelistedUnstakeAllowance + amount > maxWhitelistedAllowance) {
            revert WhitelistedAllowanceExceeded();
        }

        whitelistedUnstakeAllowance = whitelistedUnstakeAllowance + amount;
        emit WithdrawalIntentDeclared(msg.sender, amount);
```

**File:** contracts/unstaking-adapters/UnstakeStETH.sol (L48-56)
```text
    function _unstakeStEth(uint256 amountToUnstake) internal {
        stETH.safeIncreaseAllowance(address(withdrawalQueue), amountToUnstake);

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amountToUnstake;

        uint256[] memory requestIds = withdrawalQueue.requestWithdrawals(amounts, address(this));

        emit UnstakeStETHStarted(requestIds[0]);
```
