### Title
Ineffective Whitelist in `LRTConverter.declareWithdrawalIntent` — A Single Whitelisted Address Can Accumulate Unlimited Unstaking Allowance - (File: contracts/LRTConverter.sol)

---

### Summary

`LRTConverter.sol` uses a whitelist to gate who may call `declareWithdrawalIntent`, which increases the global `whitelistedUnstakeAllowance` that the operator later draws against when calling `unstakeStEth`. Because there is no per-user cap and no per-call cap, a single whitelisted address can call `declareWithdrawalIntent` an unlimited number of times, accumulating up to the hard-coded ceiling of `1_000_000_000 ether`. This renders the whitelist ineffective as a rate-limiting safety mechanism.

---

### Finding Description

`LRTConverter.sol` maintains a global counter `whitelistedUnstakeAllowance` that controls how much stETH the operator is permitted to submit for Lido withdrawal in a single `unstakeStEth` call. [1](#0-0) 

The `declareWithdrawalIntent` function is the only way to increase this counter, and it is gated by `onlyWhitelistedUser`: [2](#0-1) 

The only guard inside the function is a global ceiling of `1_000_000_000 ether`: [3](#0-2) 

There is **no per-user tracking**, **no per-call limit**, and **no cooldown**. A single whitelisted address can call `declareWithdrawalIntent(999_999_999 ether)` once, or call it thousands of times with smaller amounts, and push `whitelistedUnstakeAllowance` to the 1-billion-ETH ceiling in a single transaction.

The `withinUnstakeLimits` modifier then allows the operator to unstake up to `whitelistedUnstakeAllowance + availableActiveETHWithdrawals`: [4](#0-3) 

Once the allowance is inflated, the operator can submit a Lido withdrawal request for the entire stETH balance of the protocol in one call, far beyond what actual user withdrawal demand would justify.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The whitelist is designed to be a rate-limiting safety layer: only pre-approved entities may authorize stETH unstaking, and only up to the amount they have declared. Because a single whitelisted address can declare an arbitrarily large intent (up to 1 billion ETH), the rate-limiting guarantee is hollow. The protocol's stETH could be submitted to Lido's withdrawal queue far in excess of actual user demand, temporarily locking those assets in the queue and degrading the protocol's ability to serve depositors or other withdrawal requests during the Lido finalization window.

---

### Likelihood Explanation

**Medium.** Any address that has been granted `WHITELISTED_USER_ROLE` by the LRT manager can trigger this without any additional preconditions. The manager is expected to whitelist institutional or partner addresses, so the set of potential actors is small but non-zero. No funds need to be at risk for the actor to execute the inflation.

---

### Recommendation

1. Track declared intent per user: `mapping(address => uint256) public declaredIntentByUser` and enforce a per-user cap.
2. Alternatively, replace the global `whitelistedUnstakeAllowance` with a per-user allowance that the operator must reference explicitly.
3. Emit the caller's address and their cumulative declared amount so off-chain monitoring can detect anomalous accumulation.

---

### Proof of Concept

```solidity
// Assume `attacker` has been granted WHITELISTED_USER_ROLE by the LRT manager.

// Single call to push allowance to the ceiling:
lrtConverter.declareWithdrawalIntent(999_999_999 ether);
// whitelistedUnstakeAllowance is now 999_999_999 ether

// OR repeated calls:
for (uint i = 0; i < 1000; i++) {
    lrtConverter.declareWithdrawalIntent(1_000_000 ether);
}
// whitelistedUnstakeAllowance is now 1_000_000_000 ether (ceiling)

// Operator can now call:
lrtConverter.unstakeStEth(entireStETHBalance);
// withinUnstakeLimits passes because amountToUnstake <= whitelistedUnstakeAllowance
``` [5](#0-4) [4](#0-3)

### Citations

**File:** contracts/LRTConverter.sol (L45-54)
```text
    mapping(address => bool) private whitelistedUsers;

    uint256 public whitelistedUnstakeAllowance;

    modifier onlyWhitelistedUser() {
        if (!isUserWhitelisted(msg.sender)) {
            revert UserNotWhitelisted();
        }
        _;
    }
```

**File:** contracts/LRTConverter.sol (L58-77)
```text
    modifier withinUnstakeLimits(uint256 amountToUnstake) {
        if (amountToUnstake == 0) {
            revert InvalidAmount();
        }

        uint256 availableActiveETHWithdrawals = _getActiveETHUserWithdrawals();

        if (amountToUnstake > whitelistedUnstakeAllowance + availableActiveETHWithdrawals) {
            revert UnstakeLimitExceeded();
        }

        // Consume intended withdrawal limit
        if (whitelistedUnstakeAllowance > 0) {
            uint256 whitelistedAmountConsumed =
                amountToUnstake > whitelistedUnstakeAllowance ? whitelistedUnstakeAllowance : amountToUnstake;

            whitelistedUnstakeAllowance -= whitelistedAmountConsumed;
        }
        _;
    }
```

**File:** contracts/LRTConverter.sol (L170-177)
```text
    function unstakeStEth(uint256 amountToUnstake)
        external
        nonReentrant
        onlyLRTOperator
        withinUnstakeLimits(amountToUnstake)
    {
        _unstakeStEth(amountToUnstake);
    }
```

**File:** contracts/LRTConverter.sol (L216-227)
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
    }
```
