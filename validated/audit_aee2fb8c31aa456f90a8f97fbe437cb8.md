### Title
Missing Zero-Amount Guard in `bridgeAssetsViaNativeBridge` Allows No-Op Bridge Call with Misleading Event — (`contracts/pools/RSETHPoolNoWrapper.sol`)

### Summary

`RSETHPoolNoWrapper.bridgeAssetsViaNativeBridge()` contains no guard against a zero `ethBalanceMinusFees`. When `address(this).balance == feeEarnedInETH`, the function silently forwards 0 ETH through `UnichainMessenger.sendETHToL1ViaBridge` → `IUnichainMessenger.bridgeETHTo`, emits `BridgedETHToL1ViaNativeBridge` with `amount=0`, and delivers nothing to `l1VaultETHForL2Chain` on L1.

---

### Finding Description

**Execution path:**

1. `BRIDGER_ROLE` calls `bridgeAssetsViaNativeBridge()`.
2. `ethBalanceMinusFees = getETHBalanceMinusFees()` = `address(this).balance - feeEarnedInETH`. [1](#0-0) 
3. If `address(this).balance == feeEarnedInETH`, this returns `0`. No revert occurs.
4. `IL2Messenger(messenger).sendETHToL1ViaBridge{ value: 0 }(l2Bridge, l1VaultETHForL2Chain, 0)` is called. [2](#0-1) 
5. In `UnichainMessenger.sendETHToL1ViaBridge`, the guard `if (msg.value != value) revert MismatchedMsgValue()` passes because `0 == 0`. [3](#0-2) 
6. `IUnichainMessenger(l2bridge).bridgeETHTo{ value: 0 }(target, ...)` is called — 0 ETH forwarded to L1.
7. `BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, 0)` is emitted. [4](#0-3) 

**Contrast with sibling functions:** `bridgeTokens()` explicitly reverts with `ZeroBridgeAmount` when `balance == 0`, and `bridgeAssets()` enforces `minAmount != 0`. `bridgeAssetsViaNativeBridge()` has no equivalent guard. [5](#0-4) 

---

### Impact Explanation

The function promises to transfer user-deposited ETH (net of fees) to L1. When called with zero net balance, it executes a no-op bridge transaction, emits a `BridgedETHToL1ViaNativeBridge` event with `amount=0`, and delivers nothing to the L1 vault. Off-chain monitoring systems or accounting tools that rely on this event will record a false bridge operation. No funds are lost, matching **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

The condition `address(this).balance == feeEarnedInETH` is reachable in normal operation: after all user ETH has been bridged via `bridgeAssets()` (LayerZero path) but before new deposits arrive, only accumulated fee ETH remains. A `BRIDGER_ROLE` operator calling `bridgeAssetsViaNativeBridge()` in this state — whether by mistake or via an automated script — triggers the no-op. No malicious intent or key compromise is required.

---

### Recommendation

Add a zero-amount check at the top of `bridgeAssetsViaNativeBridge()`, consistent with `bridgeTokens()`:

```solidity
function bridgeAssetsViaNativeBridge() external nonReentrant onlyRole(BRIDGER_ROLE) {
    UtilLib.checkNonZeroAddress(l2Bridge);
    UtilLib.checkNonZeroAddress(messenger);
    UtilLib.checkNonZeroAddress(l1VaultETHForL2Chain);

    uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
+   if (ethBalanceMinusFees == 0) revert ZeroBridgeAmount();

    IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
        l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
    );

    emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
}
```

---

### Proof of Concept

```solidity
// Setup: pool.balance == feeEarnedInETH (e.g., both == 1 ether)
// All user ETH was previously bridged via bridgeAssets(); only fee ETH remains.

vm.prank(bridgerRole);
pool.bridgeAssetsViaNativeBridge();
// Expect: bridgeETHTo called with msg.value == 0
// Expect: BridgedETHToL1ViaNativeBridge emitted with amount == 0
// Expect: l1VaultETHForL2Chain received 0 ETH
```

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L437-437)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L439-441)
```text
        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L443-443)
```text
        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L506-510)
```text
        uint256 balance = getTokenBalanceMinusFees(token);

        if (balance == 0) {
            revert ZeroBridgeAmount();
        }
```

**File:** contracts/bridges/UnichainMessenger.sol (L25-26)
```text
        if (msg.value != value) revert MismatchedMsgValue();
        IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
```
