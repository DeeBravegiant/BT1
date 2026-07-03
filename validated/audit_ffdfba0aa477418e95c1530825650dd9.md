### Title
`withinUnstakeLimits` Modifier Fails to Track Consumed `assetsCommitted` Budget Across Multiple Calls, Enabling Unbounded stETH Over-Unstaking — (`contracts/LRTConverter.sol`)

---

### Summary

The `withinUnstakeLimits` modifier reads `assetsCommitted[ETH_TOKEN]` as a live snapshot on every call but **never decrements it** when `unstakeStEth` succeeds. Because `assetsCommitted` is only modified by `initiateWithdrawal` (up) and `_unlockWithdrawalRequests` (down) in `LRTWithdrawalManager`, the operator can call `unstakeStEth` an arbitrary number of times against the same committed-ETH window, unstaking multiples of the actual user-committed ETH and causing protocol insolvency.

---

### Finding Description

`withinUnstakeLimits` enforces:

```
amountToUnstake ≤ whitelistedUnstakeAllowance + assetsCommitted[ETH_TOKEN]
``` [1](#0-0) 

After the check passes it only decrements `whitelistedUnstakeAllowance`: [2](#0-1) 

It **never writes back to `assetsCommitted`**. The `assetsCommitted` mapping lives entirely in `LRTWithdrawalManager` and is only mutated by:

- `initiateWithdrawal` → `assetsCommitted[asset] += expectedAssetAmount` [3](#0-2) 
- `_unlockWithdrawalRequests` → `assetsCommitted[asset] -= request.expectedAssetAmount` [4](#0-3) 

`unstakeStEth` / `_unstakeStEth` touch neither: [5](#0-4) 

Therefore, after a successful `unstakeStEth(X)` call, `assetsCommitted[ETH_TOKEN]` is still `X`, and a second call with the same amount passes the identical check unchanged.

---

### Impact Explanation

Each `unstakeStEth` call submits a real Lido withdrawal request consuming real stETH from the contract. [6](#0-5) 

When the claimed ETH is later sent to the deposit pool via `claimStEth` → `_sendEthToDepositPool`, the protocol's ETH balance grows by `N × X` while user withdrawal obligations remain `X`. The surplus ETH is accounted as protocol assets, inflating rsETH NAV. When users attempt to complete their ETH withdrawals, the `LRTWithdrawalManager` must pay them from its own balance; the over-unstaked ETH has already been routed to the deposit pool and re-priced into rsETH, leaving the withdrawal manager insolvent for the original claimants. This is **Critical — Protocol insolvency**.

---

### Likelihood Explanation

The operator role (`onlyLRTOperator`) is required. However, no external compromise is needed: the operator acts entirely within their normal on-chain capabilities. The `withinUnstakeLimits` modifier exists specifically to constrain operator behaviour; its failure to track consumed budget means the constraint is illusory. A single operator transaction can call `unstakeStEth` twice in sequence with the same amount and both calls succeed. The scenario does not require front-running, MEV, or any user cooperation.

---

### Recommendation

Introduce a storage variable in `LRTConverter` that tracks how much of `assetsCommitted[ETH_TOKEN]` has already been allocated to pending Lido withdrawal requests, and decrement it inside `withinUnstakeLimits`. Alternatively, maintain a `stETHUnstakingInFlight` counter that is incremented on `unstakeStEth` and decremented on `claimStEth`, and enforce:

```
amountToUnstake ≤ whitelistedUnstakeAllowance
                + assetsCommitted[ETH_TOKEN]
                - stETHUnstakingInFlight
```

This ensures each unit of `assetsCommitted` can only back one Lido withdrawal request at a time.

---

### Proof of Concept

```
State: assetsCommitted[ETH_TOKEN] = 100e18, whitelistedUnstakeAllowance = 0
       LRTConverter holds ≥ 200e18 stETH

Tx 1 (operator): unstakeStEth(100e18)
  → modifier: 100e18 ≤ 0 + 100e18  ✓
  → _unstakeStEth submits Lido request #1 for 100e18 stETH
  → assetsCommitted[ETH_TOKEN] unchanged = 100e18

Tx 2 (operator): unstakeStEth(100e18)
  → modifier: 100e18 ≤ 0 + 100e18  ✓  (same snapshot)
  → _unstakeStEth submits Lido request #2 for 100e18 stETH
  → assetsCommitted[ETH_TOKEN] unchanged = 100e18

Total stETH queued for unstaking: 200e18
Total ETH committed to users:      100e18
Over-unstaking ratio:              2×

Assert: totalUnstaked (200e18) > assetsCommitted (100e18)  → PASS (invariant broken)
```

The fork-test sequence from the question (user initiates → operator unstakes → user completes → operator unstakes again) is a valid but unnecessarily complex path; the simpler two-consecutive-call path above demonstrates the same root cause without requiring any user action between operator calls. [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/LRTConverter.sol (L266-270)
```text
    function _getActiveETHUserWithdrawals() internal view returns (uint256 activeETHWithdrawals) {
        ILRTWithdrawalManager lrtWithdrawalManager =
            ILRTWithdrawalManager(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));
        activeETHWithdrawals = lrtWithdrawalManager.assetsCommitted(LRTConstants.ETH_TOKEN);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L173-173)
```text
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L802-802)
```text
            assetsCommitted[asset] -= request.expectedAssetAmount;
```

**File:** contracts/unstaking-adapters/UnstakeStETH.sol (L48-57)
```text
    function _unstakeStEth(uint256 amountToUnstake) internal {
        stETH.safeIncreaseAllowance(address(withdrawalQueue), amountToUnstake);

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amountToUnstake;

        uint256[] memory requestIds = withdrawalQueue.requestWithdrawals(amounts, address(this));

        emit UnstakeStETHStarted(requestIds[0]);
    }
```
