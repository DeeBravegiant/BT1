### Title
Unguarded Zero-Value Calls Enable Block Stuffing — (`contracts/bridges/OptimismMessenger.sol`)

---

### Summary

`OptimismMessenger.sendETHToL1ViaBridge` has no access control and no minimum-value guard. An unprivileged attacker can call it with `value=0` and `msg.value=0` in a tight loop, consuming Optimism L2 block gas at near-zero cost and delaying the `BRIDGER_ROLE`'s legitimate `bridgeAssetsViaNativeBridge()` call.

---

### Finding Description

`OptimismMessenger.sendETHToL1ViaBridge` contains only one guard:

```solidity
if (msg.value != value) revert MismatchedMsgValue();
``` [1](#0-0) 

When `value=0` and `msg.value=0`, the condition `0 != 0` is false — no revert. Execution proceeds to the external bridge call with 0 ETH. The attacker also controls the `l2bridge` argument; passing a contract that accepts zero-value calls makes each spam transaction complete successfully (no revert), maximizing gas consumed per call.

Compare with `LineaMessenger`, which explicitly blocks this path:

```solidity
if (value == 0) revert ZeroAmount();
``` [2](#0-1) 

`OptimismMessenger` and `BaseMessenger` are both missing this guard. The legitimate bridging path that would be delayed is:

```solidity
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: amount }(l2Bridge, l1VaultETHForL2Chain, amount);
``` [3](#0-2) 

which is called from `bridgeAssetsViaNativeBridge`, a `BRIDGER_ROLE`-only function. [4](#0-3) 

---

### Impact Explanation

An attacker can fill Optimism L2 blocks with zero-cost spam calls, temporarily preventing the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge()` transaction from landing. Pool ETH accumulated on L2 remains unbridged for the duration of the attack. No funds are permanently lost; the delay is temporary. This maps to **Low — Block stuffing**.

---

### Likelihood Explanation

- Optimism L2 gas is extremely cheap (fractions of a cent per transaction).
- No privileges, no ETH, and no special setup are required.
- The attacker controls `l2bridge`, so they can use a mock contract to avoid reverts and maximize gas consumption per call.
- The missing guard is a clear inconsistency with `LineaMessenger`, which already has the fix.

---

### Recommendation

Add a zero-value guard and a non-zero address check to `OptimismMessenger.sendETHToL1ViaBridge`, mirroring `LineaMessenger`:

```solidity
function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    UtilLib.checkNonZeroAddress(l2bridge);
    UtilLib.checkNonZeroAddress(target);
    if (value == 0) revert ZeroAmount();
    if (msg.value != value) revert MismatchedMsgValue();
    IOptimismMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
}
```

Apply the same fix to `BaseMessenger` and `ScrollMessenger`, which share the same missing guard. [5](#0-4) [6](#0-5) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test: OP mainnet fork
// 1. Deploy a mock bridge that accepts 0-value bridgeETHTo calls without reverting
contract MockOpBridge {
    function bridgeETHTo(address, uint32, bytes memory) external payable {}
}

// 2. In the test:
function testBlockStuffing() public {
    MockOpBridge mockBridge = new MockOpBridge();
    OptimismMessenger messenger = OptimismMessenger(DEPLOYED_MESSENGER);

    // Attacker spams N zero-value calls per block
    for (uint i = 0; i < 600; i++) {
        // msg.value=0, value=0 — passes MismatchedMsgValue check
        messenger.sendETHToL1ViaBridge(address(mockBridge), address(0xdead), 0);
    }

    // Assert: BRIDGER_ROLE's bridgeAssetsViaNativeBridge() cannot land in the same block
    // Pool ETH remains unbridged
}
```

The `msg.value=0, value=0` path bypasses the only guard at line 25, and the mock bridge prevents revert at line 26, allowing each spam call to consume the full function gas. At Optimism's typical L2 gas prices, filling a block costs the attacker negligible ETH. [1](#0-0)

### Citations

**File:** contracts/bridges/OptimismMessenger.sol (L24-27)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IOptimismMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```

**File:** contracts/bridges/LineaMessenger.sol (L35-35)
```text
        if (value == 0) revert ZeroAmount();
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L466-480)
```text
    function bridgeAssetsViaNativeBridge(uint256 amount) external nonReentrant onlyRole(BRIDGER_ROLE) {
        UtilLib.checkNonZeroAddress(l2Bridge);
        UtilLib.checkNonZeroAddress(messenger);
        UtilLib.checkNonZeroAddress(l1VaultETHForL2Chain);

        if (amount == 0) revert InvalidAmount();

        // bridge up to the ETH balance minus fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
        if (amount > ethBalanceMinusFees) revert InsufficientETHBalance();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: amount }(l2Bridge, l1VaultETHForL2Chain, amount);

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, amount);
    }
```

**File:** contracts/bridges/BaseMessenger.sol (L23-26)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IBaseMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
    }
```

**File:** contracts/bridges/ScrollMessenger.sol (L21-24)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
    }
```
