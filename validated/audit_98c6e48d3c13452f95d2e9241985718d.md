Audit Report

## Title
`lastBridgedDepositId` Unconditionally Advances to `counter - 1` Without Verifying `amount` Covers All Pending Deposits - (`contracts/KERNEL/KernelVaultETH.sol`)

## Summary

`bridgeKernelToBSC` sets `lastBridgedDepositId = counter - 1` unconditionally at execution time, with no on-chain check that the operator-supplied `amount` equals the sum of all deposit records from `lastBridgedDepositId + 1` to `counter - 1`. Any deposit that mines between the operator's off-chain amount calculation and the bridge transaction being included will have its tokens excluded from the bridged batch while `lastBridgedDepositId` advances past its ID, corrupting the deposit-tracking invariant and leaving the user's tokens stranded in the contract with no on-chain recovery signal.

## Finding Description

In `bridgeKernelToBSC`, line 262 snapshots the current deposit frontier unconditionally:

```solidity
lastBridgedDepositId = counter - 1;   // line 262
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);  // line 264
``` [1](#0-0) 

The only validation on `amount` is a balance check against the contract's total token holdings, not against the sum of pending deposit records:

```solidity
if (kernel.balanceOf(address(this)) < amount) {
    revert InsufficientKernelBalance();
}
``` [2](#0-1) 

Meanwhile, `_depositKernel` atomically increments `counter` with each user deposit:

```solidity
uint256 depositId = counter;
userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
++counter;
``` [3](#0-2) 

Because the operator must compute `amount` off-chain before submitting the bridge transaction, any `depositKernel` call that mines in the same block or before the bridge transaction will increment `counter`, causing `lastBridgedDepositId` to advance to the new `counter - 1` even though the newly-added deposit's tokens were not included in `amount`. The `BridgedKernelToBSC` event then emits the advanced `lastBridgedDepositId`, misleading the BSC-side receiver into believing all deposits up to that ID were bridged. [4](#0-3) 

There is no on-chain mechanism to re-bridge specifically for a skipped deposit ID, since `lastBridgedDepositId` has already moved past it and subsequent bridge calls will advance it further.

## Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The skipped deposit's tokens remain in `KernelVaultETH` (no direct theft or permanent on-chain loss), but `lastBridgedDepositId` has advanced past the deposit's ID. The BSC-side accounting, which relies on `lastBridgedDepositId` from the `BridgedKernelToBSC` event, records the deposit as bridged when it was not. The user does not receive their tokens on BSC as promised, and the off-chain bookkeeping is corrupted with no on-chain recovery path signaled by the contract state.

## Likelihood Explanation

No malicious actor is required. This is a natural race condition on any live chain with non-trivial deposit activity: the operator reads `counter` and sums deposit amounts off-chain, then a normal user's `depositKernel` transaction mines before the bridge transaction is included. The operator acted correctly with the information available at query time. The condition is repeatable on every bridge batch where at least one deposit arrives in the mempool concurrently.

## Recommendation

Accept an explicit `upToDepositId` parameter from the operator instead of computing `counter - 1` at execution time. Set `lastBridgedDepositId = upToDepositId` only after verifying `upToDepositId < counter` and that `amount` equals the on-chain sum of `userDeposits[i].amount` for `i` from `lastBridgedDepositId + 1` to `upToDepositId`. This eliminates the race window entirely by anchoring both the amount and the frontier to the same operator-committed range.

## Proof of Concept

```
Initial state:
  counter = 5, lastBridgedDepositId = 4
  (deposits 0–4 already bridged)

Step 1: Operator reads counter=5 off-chain, computes amount=0 (no new deposits).
Step 2: User calls depositKernel(1000). Mines first.
        → counter = 6, userDeposits[5] = {user, 1000}
Step 3: Operator's bridgeKernelToBSC(1, 1, fee, refund) mines.
        (amount=1 passes balanceOf check: contract holds 1000 KERNEL)
        → lastBridgedDepositId = counter - 1 = 5
        → kernelOftAdapter.send bridges only 1 KERNEL

Result:
  lastBridgedDepositId == 5  (on-chain)
  userDeposits[5].amount == 1000  (on-chain)
  tokens actually bridged for deposit[5] == 1
  BridgedKernelToBSC event emits lastBridgedDepositId=5 → BSC side records deposit 5 as bridged
  999 KERNEL tokens remain in KernelVaultETH with no on-chain recovery path
```

**Foundry test plan:** Deploy `KernelVaultETH` with a mock `IKERNEL_OFTAdapter`. Set `counter=5`, `lastBridgedDepositId=4`. Call `depositKernel(1000)` from a user address. Then call `bridgeKernelToBSC(1, 1, fee, refund)` from the operator. Assert `lastBridgedDepositId == 5`, `kernel.balanceOf(address(vault)) == 999`, and that the `BridgedKernelToBSC` event emitted `lastBridgedDepositId=5` with `amount=1`.

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L238-240)
```text
        if (kernel.balanceOf(address(this)) < amount) {
            revert InsufficientKernelBalance();
        }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L262-264)
```text
        lastBridgedDepositId = counter - 1;

        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L266-266)
```text
        emit BridgedKernelToBSC(dstLzChainId, receiver, amount, minAmount, nativeFee, lastBridgedDepositId);
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L391-394)
```text
        uint256 depositId = counter;

        userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
        ++counter;
```
