### Title
`lastBridgedDepositId` Unconditionally Advances to `counter - 1` Regardless of Bridged Amount, Silently Skipping Deposits Made Between Off-Chain Calculation and Transaction Mining - (`contracts/KERNEL/KernelVaultETH.sol`)

---

### Summary

`bridgeKernelToBSC` always sets `lastBridgedDepositId = counter - 1` at the time of execution, with no check that the operator-supplied `amount` actually covers every deposit from the previous `lastBridgedDepositId + 1` up to `counter - 1`. A deposit that arrives between the operator's off-chain amount calculation and the transaction being mined will be silently excluded from the bridged amount while `lastBridgedDepositId` advances past it.

---

### Finding Description

In `bridgeKernelToBSC`, line 262 unconditionally snapshots the current deposit frontier:

```solidity
lastBridgedDepositId = counter - 1;   // line 262
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);  // line 264
``` [1](#0-0) 

The `amount` parameter is supplied by the operator and is validated only against the contract's token balance (`balanceOf >= amount`), not against the sum of all pending deposit records from `lastBridgedDepositId + 1` to `counter - 1`. [2](#0-1) 

Meanwhile, `_depositKernel` increments `counter` atomically with each deposit: [3](#0-2) 

Because the operator must compute `amount` off-chain before submitting the transaction, any deposit that lands in the mempool and mines before the bridge transaction will increment `counter`, causing `lastBridgedDepositId` to advance past that deposit ID even though its tokens were not included in `amount`.

---

### Impact Explanation

The tokens from the skipped deposit remain in the `KernelVaultETH` contract — they are not lost. However, `lastBridgedDepositId` now equals or exceeds the skipped deposit's ID, so the BSC-side receiver has no on-chain signal that those tokens were never sent. The user's deposit is silently excluded from the current bridge batch, and the off-chain bookkeeping (which relies on `lastBridgedDepositId` from the `BridgedKernelToBSC` event) incorrectly records it as bridged. [4](#0-3) 

This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

This does not require a malicious operator. It is a natural race condition: the operator reads `counter` and sums deposit amounts off-chain, then a user's `depositKernel` transaction mines first, incrementing `counter` before the bridge transaction is included. The operator acted correctly with the information available at query time. On a live chain with any non-trivial deposit activity, this race is realistic.

---

### Recommendation

Capture `lastBridgedDepositId` as a local variable at the start of `bridgeKernelToBSC` and compute the expected bridgeable amount on-chain by summing `userDeposits[i].amount` for `i` from `lastBridgedDepositId + 1` to `counter - 1`, then require `amount == sum`. Alternatively, accept an explicit `upToDepositId` parameter from the operator and set `lastBridgedDepositId = upToDepositId` only after verifying `upToDepositId < counter` and that `amount` matches the sum of deposits in that range.

---

### Proof of Concept

```
State before:
  counter = 5, lastBridgedDepositId = 4
  userDeposits[0..4] each = 100 KERNEL (total 500 bridged in batch 1)

Step 1: Operator reads counter=5 off-chain, computes amount=0 (no new deposits yet).
Step 2: User calls depositKernel(1000). Mines first.
        → counter = 6, userDeposits[5] = {user, 1000}

Step 3: Operator's bridgeKernelToBSC(1, 1, fee, refund) mines.
        (amount=1 satisfies balanceOf check since contract holds 1000 KERNEL)
        → lastBridgedDepositId = counter - 1 = 5
        → kernelOftAdapter.send bridges only 1 KERNEL token

Result:
  lastBridgedDepositId == 5  ✓ (on-chain)
  deposit[5].amount == 1000  ✓ (on-chain)
  tokens actually bridged for deposit[5] == 1  ✗
  BridgedKernelToBSC event emits lastBridgedDepositId=5, misleading BSC side
  999 KERNEL tokens remain stranded in KernelVaultETH with no recovery path
  signaled by lastBridgedDepositId
``` [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L225-267)
```text
    function bridgeKernelToBSC(
        uint256 amount,
        uint256 minAmount,
        uint256 nativeFee,
        address refundAddress
    )
        external
        payable
        nonReentrant
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(refundAddress);

        if (kernel.balanceOf(address(this)) < amount) {
            revert InsufficientKernelBalance();
        }

        if (minAmount > amount || minAmount == 0) {
            revert InvalidMinAmount();
        }

        if (msg.value < nativeFee) {
            revert InsufficientNativeFee();
        }

        SendParam memory sendParam = SendParam({
            dstEid: dstLzChainId,
            to: getReceiver(),
            amountLD: amount,
            minAmountLD: minAmount,
            extraOptions: bytes(""),
            composeMsg: bytes(""),
            oftCmd: bytes("")
        });

        MessagingFee memory fee = MessagingFee({ nativeFee: nativeFee, lzTokenFee: 0 });

        lastBridgedDepositId = counter - 1;

        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);

        emit BridgedKernelToBSC(dstLzChainId, receiver, amount, minAmount, nativeFee, lastBridgedDepositId);
    }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L391-394)
```text
        uint256 depositId = counter;

        userDeposits[depositId] = UserDeposit({ user: user, amount: amount });
        ++counter;
```
