Audit Report

## Title
Unguarded `sendETHToL1ViaBridge` Accepts Arbitrary `l2bridge`, Enabling Block Stuffing — (`contracts/bridges/UnichainMessenger.sol`)

## Summary

`UnichainMessenger.sendETHToL1ViaBridge` is `external payable nonReentrant` with no role guard and no validation of the caller-supplied `l2bridge` address. An attacker can pass a malicious contract whose `bridgeETHTo` triggers `INVALID` (consuming all forwarded gas) with only 1 wei, stuffing consecutive Unichain blocks and delaying the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` transactions.

## Finding Description

`sendETHToL1ViaBridge` performs only a `msg.value == value` check before making an unbounded external call to the attacker-controlled `l2bridge`: [1](#0-0) 

`DEFAULT_GAS_LIMIT = 200_000` is passed as the `_minGasLimit` parameter inside the `bridgeETHTo` calldata — it is the L1 execution gas hint, not an EVM gas stipend on the L2 call itself. [2](#0-1) 

The EVM forwards all remaining gas (minus 1/64 per EIP-150) to `l2bridge.bridgeETHTo`. A malicious contract implementing `bridgeETHTo` with `assembly { invalid() }` consumes all of it. The legitimate caller path (`bridgeAssetsViaNativeBridge`) is protected by `onlyRole(BRIDGER_ROLE)` and uses the admin-configured `l2Bridge` storage variable: [3](#0-2) 

But `UnichainMessenger` itself has no such protection, so the attacker bypasses the pool entirely. The identical pattern exists in `BaseMessenger`: [4](#0-3) 

## Impact Explanation

**Low — Block stuffing.** An attacker with 1 wei can submit transactions that consume the full Unichain block gas limit. Repeated submissions across consecutive blocks delay or prevent the `BRIDGER_ROLE` from landing `bridgeAssetsViaNativeBridge`, temporarily disrupting the ETH bridging flow. No user funds are directly stolen or permanently frozen.

## Likelihood Explanation

The function is permissionlessly callable with no prerequisites beyond 1 wei. Unichain gas costs are low, making repeated block stuffing economically feasible. The path requires no privileged access, no front-running, no external protocol compromise, and no victim mistakes. The attack is direct and immediately reproducible.

## Recommendation

Restrict `sendETHToL1ViaBridge` to `BRIDGER_ROLE`, or store the canonical bridge address immutably and validate against it:

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

Apply the same fix to `BaseMessenger`, `OptimismMessenger`, `ArbitrumMessenger`, and `ScrollMessenger`.

## Proof of Concept

```solidity
contract GasBombBridge {
    function bridgeETHTo(address, uint32, bytes memory) external payable {
        assembly { invalid() } // consumes all forwarded gas
    }
    receive() external payable {}
}

contract Exploit {
    function run(address unichainMessenger) external payable {
        GasBombBridge bomb = new GasBombBridge();
        IUnichainMessenger(unichainMessenger).sendETHToL1ViaBridge{value: 1}(
            address(bomb), address(0xdead), 1
        );
        // Transaction consumes full block gas. Repeat to stuff consecutive blocks.
    }
}
```

Foundry fork test on Unichain: deploy `GasBombBridge`, call `run` with 1 wei, assert `gasleft()` after the call is near zero. Repeat in a loop to demonstrate consecutive block stuffing preventing `bridgeAssetsViaNativeBridge` inclusion.

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
