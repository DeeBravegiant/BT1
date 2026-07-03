### Title
Operator Can Permanently Lock Excess ETH in `KernelVaultETH` Due to Lax Payment Validation in `bridgeKernelToBSC()` - (File: contracts/KERNEL/KernelVaultETH.sol)

---

### Summary

`KernelVaultETH.bridgeKernelToBSC()` validates the native fee with `msg.value < nativeFee` instead of `msg.value != nativeFee`. This allows the caller to send more ETH than required. The excess ETH is not forwarded to the OFT adapter and is not refunded — it remains permanently locked in the `KernelVaultETH` contract, which has no ETH recovery mechanism.

---

### Finding Description

In `KernelVaultETH.bridgeKernelToBSC()`, the fee validation is:

```solidity
if (msg.value < nativeFee) {
    revert InsufficientNativeFee();
}
```

Only `nativeFee` is forwarded to the OFT adapter:

```solidity
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
```

If `msg.value > nativeFee`, the difference `msg.value - nativeFee` stays in the `KernelVaultETH` contract. The contract has no `receive()` fallback, no `withdrawETH()`, no `rescue()`, and no other mechanism to recover stranded ETH. The only payable function is `bridgeKernelToBSC()` itself. Any excess ETH is permanently frozen.

This is the direct analog of the reference report: a `>=`/`<` check where `==`/`!=` is required, causing overpayment to be silently absorbed rather than rejected or refunded.

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Any ETH sent in excess of `nativeFee` is irrecoverably locked in `KernelVaultETH`. There is no admin function, no sweep function, and no `receive()` that could be used to drain or recover the ETH. The contract is upgradeable, but the current implementation provides no path to recover stranded ETH.

---

### Likelihood Explanation

The `OPERATOR_ROLE` calls `bridgeKernelToBSC()` in normal protocol operation. The `nativeFee` is a dynamic LayerZero quote that can change between the time `getNativeFee()` is called and the time the transaction is submitted (e.g., due to gas price fluctuations, network congestion, or a stale quote). An operator who adds a small buffer to ensure the transaction does not revert due to `InsufficientNativeFee` will silently lose the excess ETH. This is a realistic operational scenario, not a theoretical one.

---

### Recommendation

Replace the lax check with an exact equality check, mirroring the pattern already used correctly in other contracts in this codebase (e.g., `RSETHPool.bridgeAssets()`, `L1Vault.bridgeRsETHToL2()`, `L1VaultV2.bridgeRsETHToL2()`):

```solidity
// Before (vulnerable):
if (msg.value < nativeFee) {
    revert InsufficientNativeFee();
}

// After (fixed):
if (msg.value != nativeFee) {
    revert InsufficientNativeFee();
}
```

Alternatively, refund any excess ETH to `refundAddress` after the send call.

---

### Proof of Concept

1. Operator calls `getNativeFee(amount, minAmount)` and receives `quotedFee = 0.01 ETH`.
2. Operator submits `bridgeKernelToBSC(amount, minAmount, 0.01 ether, refundAddress)` with `msg.value = 0.02 ether` (adding a buffer to avoid reversion).
3. The check `msg.value < nativeFee` → `0.02 < 0.01` → `false`, so no revert.
4. `kernelOftAdapter.send{ value: 0.01 ether }(...)` is called — only `nativeFee` is forwarded.
5. The remaining `0.01 ETH` stays in `KernelVaultETH` with no recovery path.

**Vulnerable lines:** [1](#0-0) 

**Only `nativeFee` forwarded, not `msg.value`:** [2](#0-1) 

**No ETH recovery mechanism exists in the contract** — the only payable entry point is `bridgeKernelToBSC()`: [3](#0-2) 

**Contrast with correct pattern used elsewhere in the codebase:** [4](#0-3)

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

**File:** contracts/L1VaultV2.sol (L314-316)
```text
        if (msg.value != nativeFee) {
            revert IncorrectNativeFee();
        }
```
