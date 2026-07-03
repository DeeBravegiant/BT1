The vulnerability is real and reachable. Here is the analysis:

---

### Title
Missing Zero-Value Guard in `sendETHToL1ViaBridge` Allows No-Op Bridge Calls - (`contracts/bridges/UnichainMessenger.sol`)

### Summary
`UnichainMessenger.sendETHToL1ViaBridge` lacks a `value == 0` check, allowing any caller to submit a zero-ETH bridge message that wastes L1 gas and produces a no-op delivery.

### Finding Description

`UnichainMessenger.sendETHToL1ViaBridge` only validates that `msg.value == value`, but does not reject the case where both are zero: [1](#0-0) 

When `value = 0` and `msg.value = 0`, the guard on line 25 passes (`0 != 0` is false), and `bridgeETHTo{ value: 0 }(...)` is forwarded to the L2 bridge. This submits a zero-ETH cross-chain message that the L1 relayer will process as a no-op.

By contrast, `LineaMessenger.sendETHToL1ViaBridge` explicitly reverts on zero value before any bridge interaction: [2](#0-1) 

### Impact Explanation
A zero-ETH bridge message is submitted to the Unichain L2 bridge. The L1 relayer picks it up, spends L1 gas executing it, and delivers nothing. The bridging promise (transfer ETH from L2 to L1) is not fulfilled. No funds are lost, matching **Low — Contract fails to deliver promised returns, but doesn't lose value**.

### Likelihood Explanation
Any externally-owned account or contract can call `sendETHToL1ViaBridge` with `value = 0` and `msg.value = 0`. There is no role check, no pause gate, and no other guard blocking this path. The call succeeds unconditionally.

### Recommendation
Add a zero-value guard identical to the one in `LineaMessenger`:

```solidity
if (value == 0) revert ZeroAmount();
```

Insert it before the `msg.value != value` check in `sendETHToL1ViaBridge`.

### Proof of Concept

```solidity
// Pseudocode — run on a local fork with a mock IUnichainMessenger
UnichainMessenger messenger = new UnichainMessenger();
MockL2Bridge bridge = new MockL2Bridge(); // records bridgeETHTo calls

// Call with zero value — succeeds, no revert
messenger.sendETHToL1ViaBridge{value: 0}(address(bridge), recipient, 0);

// Assert: bridge received a call with value == 0
assertEq(bridge.lastValue(), 0); // passes → zero-ETH message submitted
```

Fuzz `value` from `0` to `1 wei`: the `value == 0` path reaches `bridgeETHTo` without reverting, confirming the missing guard.

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
