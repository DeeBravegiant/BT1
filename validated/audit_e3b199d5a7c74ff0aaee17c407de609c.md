### Title
Missing Zero-Value Guard Allows No-Op Bridge Call - (`contracts/bridges/UnichainMessenger.sol`)

### Summary

`UnichainMessenger.sendETHToL1ViaBridge` lacks a `value == 0` revert guard, allowing a caller to submit a zero-ETH bridge message to L1. The sibling `LineaMessenger` explicitly blocks this with `if (value == 0) revert ZeroAmount()`, confirming the omission is unintentional.

### Finding Description

In `UnichainMessenger.sendETHToL1ViaBridge`, the only input validation is:

```solidity
if (msg.value != value) revert MismatchedMsgValue();
``` [1](#0-0) 

When a caller passes `value = 0` and `msg.value = 0`, this check trivially passes (`0 == 0`), and execution proceeds to:

```solidity
IUnichainMessenger(l2bridge).bridgeETHTo{ value: 0 }(target, DEFAULT_GAS_LIMIT, bytes(""));
``` [2](#0-1) 

This dispatches a zero-ETH withdrawal message to the Unichain native bridge with a 200,000 gas limit, producing a no-op delivery on L1 that burns L1 gas without transferring any ETH.

By contrast, `LineaMessenger.sendETHToL1ViaBridge` explicitly guards against this:

```solidity
if (value == 0) revert ZeroAmount();
``` [3](#0-2) 

The `IL2Messenger` interface documents the intent as "The amount of ETH to send", implying a non-zero transfer is the contract's promise. [4](#0-3) 

### Impact Explanation

The contract fails to deliver its promised return (bridging ETH to L1) without losing value. A zero-ETH bridge message is accepted and forwarded, wasting L1 gas and producing a no-op that does not fulfill the bridging purpose. This matches the **Low** scope: *Contract fails to deliver promised returns, but doesn't lose value*.

### Likelihood Explanation

Any external caller can trigger this with zero cost (no ETH required, no role needed). The function is `external payable` with no access control. [5](#0-4) 

### Recommendation

Add a zero-value guard mirroring `LineaMessenger`:

```solidity
if (value == 0) revert ZeroAmount();
```

Place it before the `msg.value != value` check in `sendETHToL1ViaBridge`.

### Proof of Concept

```solidity
// Call with value=0 and msg.value=0 — passes all checks, submits zero-ETH bridge message
unichainMessenger.sendETHToL1ViaBridge{value: 0}(l2bridge, target, 0);
// bridgeETHTo{value: 0}(...) is called — no ETH bridged, L1 gas wasted
```

The `msg.value != value` check passes (`0 != 0` is false), so no revert occurs and the zero-value call reaches the bridge. [6](#0-5)

### Citations

**File:** contracts/bridges/UnichainMessenger.sol (L24-27)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```

**File:** contracts/bridges/LineaMessenger.sol (L35-35)
```text
        if (value == 0) revert ZeroAmount();
```

**File:** contracts/interfaces/L2/IL2Messenger.sol (L14-18)
```text
     * @param l2bridge The address of the L2 bridge contract
     * @param target The address of the recipient on L1
     * @param value The amount of ETH to send
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable;
```
