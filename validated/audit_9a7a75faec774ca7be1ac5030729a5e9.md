### Title
Excess ETH Permanently Locked in `KernelVaultETH` When `msg.value > nativeFee` in `bridgeKernelToBSC` — (`contracts/KERNEL/KernelVaultETH.sol`)

---

### Summary

`KernelVaultETH.bridgeKernelToBSC` accepts `msg.value >= nativeFee` but only forwards exactly `nativeFee` to the LayerZero OFT adapter. The contract has no `receive()`, `fallback()`, or ETH-recovery function, so any excess ETH (`msg.value - nativeFee`) is permanently locked.

---

### Finding Description

The guard at line 246 only rejects underpayment:

```solidity
if (msg.value < nativeFee) {
    revert InsufficientNativeFee();
}
``` [1](#0-0) 

The subsequent call forwards only the caller-supplied `nativeFee` parameter, not `msg.value`:

```solidity
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
``` [2](#0-1) 

The contract inherits only `Initializable`, `AccessControlUpgradeable`, `PausableUpgradeable`, and `ReentrancyGuardUpgradeable` — none of which provide ETH recovery — and defines no `receive()`, `fallback()`, or sweep function anywhere in the file. [3](#0-2) 

The `refundAddress` parameter is passed to the LayerZero adapter (which may refund its own internal excess), but the vault itself never refunds `msg.value - nativeFee` to anyone.

---

### Impact Explanation

Any ETH sent above the exact `nativeFee` is irrecoverably locked in `KernelVaultETH`. Over repeated bridge operations — especially if the operator adds a small buffer to avoid `LZ_InsufficientFee` reverts — the locked balance accumulates with no on-chain path to recover it. This constitutes **permanent freezing of funds**.

---

### Likelihood Explanation

The operator is a trusted role, but the scenario does not require malice or key compromise. It is standard operational practice to send a small ETH buffer when paying LayerZero fees to guard against fee fluctuations between `quoteSend` and execution. The contract's own `getNativeFee` view function returns a point-in-time quote; any operator who adds even 1 wei of buffer triggers the lock. The path is reachable on every bridge call.

---

### Recommendation

Replace the `>=` guard with strict equality, or refund the excess to `refundAddress` (or `msg.sender`) before returning:

```solidity
// Option A: strict equality
if (msg.value != nativeFee) revert InvalidNativeFee();

// Option B: refund excess
if (msg.value < nativeFee) revert InsufficientNativeFee();
kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
uint256 excess = msg.value - nativeFee;
if (excess > 0) {
    (bool ok,) = refundAddress.call{ value: excess }("");
    require(ok, "ETH refund failed");
}
```

---

### Proof of Concept

```solidity
// Local fork / unit test (no mainnet)
function test_excessEthLocked() public {
    uint256 quotedFee = kernelVaultETH.getNativeFee(amount, minAmount);

    vm.prank(operator);
    kernelVaultETH.bridgeKernelToBSC{value: quotedFee + 1 ether}(
        amount, minAmount, quotedFee, refundAddress
    );

    // Excess ETH is now permanently locked
    assertEq(address(kernelVaultETH).balance, 1 ether);

    // No function exists to withdraw it — all recovery attempts revert
}
```

The `bridgeKernelToBSC` call succeeds (the `msg.value < nativeFee` check passes), the adapter receives exactly `quotedFee`, and the 1 ETH surplus sits in the vault forever.

### Citations

**File:** contracts/KERNEL/KernelVaultETH.sol (L21-21)
```text
contract KernelVaultETH is Initializable, AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L246-248)
```text
        if (msg.value < nativeFee) {
            revert InsufficientNativeFee();
        }
```

**File:** contracts/KERNEL/KernelVaultETH.sol (L264-264)
```text
        kernelOftAdapter.send{ value: nativeFee }(sendParam, fee, refundAddress);
```
