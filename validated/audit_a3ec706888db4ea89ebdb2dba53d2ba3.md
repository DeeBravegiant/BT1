### Title
Missing Zero-Amount Guard in `bridgeAssetsViaNativeBridge()` Allows Emission of Misleading Zero-ETH Bridge Event — (`contracts/pools/RSETHPool.sol`, `RSETHPoolV2.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`)

---

### Summary

`bridgeAssetsViaNativeBridge()` (and its equivalent `bridgeAssets()` in `RSETHPoolV2`) does not check whether `getETHBalanceMinusFees()` is zero before forwarding the value to `IL2Messenger.sendETHToL1ViaBridge`. When all pool ETH equals accumulated fees, the call succeeds with `{value: 0}` and emits `BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, 0)`, delivering nothing to L1Vault while logging a bridge event.

---

### Finding Description

Every `bridgeAssetsViaNativeBridge()` implementation follows the same pattern:

```solidity
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();   // may be 0

IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
    l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
);

emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`getETHBalanceMinusFees()` is simply `address(this).balance - feeEarnedInETH`. [5](#0-4) 

When `feeEarnedInETH == address(this).balance` (all pool ETH is accounted as fees) or when the pool holds no ETH at all, this returns `0`. The `IL2Messenger` interface only defines a `MismatchedMsgValue` guard — which checks `msg.value == value`. Since both are `0`, the check passes silently. [6](#0-5) 

The result: the event `BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, 0)` is emitted, L1Vault receives nothing, and off-chain accounting systems that rely on this event are misled.

By contrast, the sibling function `bridgeTokens()` correctly guards against this:

```solidity
if (balance == 0) {
    revert ZeroBridgeAmount();
}
``` [7](#0-6) 

No equivalent guard exists in any `bridgeAssetsViaNativeBridge()` implementation.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A `BridgedETHToL1ViaNativeBridge` event is emitted with `amount = 0`. No ETH moves to L1Vault. The pool's ETH (fees) remains intact, so no funds are lost. However, any off-chain system (keeper bots, accounting dashboards, L1Vault reconciliation logic) that trusts this event as proof of a non-zero transfer will record a phantom bridge operation.

---

### Likelihood Explanation

The precondition is reachable through normal operation:

1. Pool has received deposits; `feeEarnedInETH` has accumulated.
2. A BRIDGER_ROLE caller previously called `moveAssetsForBridging()` (or `bridgeAssets()` via Stargate) to move all non-fee ETH out, leaving `address(this).balance == feeEarnedInETH`.
3. The same or another BRIDGER_ROLE caller then calls `bridgeAssetsViaNativeBridge()` — the function executes, emits the event with `amount = 0`, and returns successfully.

No compromise of any privileged key is required beyond the legitimately-held `BRIDGER_ROLE`.

---

### Recommendation

Add a zero-amount guard at the top of `bridgeAssetsViaNativeBridge()` in all four pool contracts, consistent with the guard already present in `bridgeTokens()`:

```solidity
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
if (ethBalanceMinusFees == 0) revert ZeroBridgeAmount();
```

---

### Proof of Concept

```solidity
// Local unit test (no mainnet required)
function test_bridgeAssetsViaNativeBridge_zeroAmount() public {
    // Setup: pool has ETH equal exactly to accumulated fees
    uint256 fee = 1 ether;
    vm.deal(address(pool), fee);
    // Simulate fee accumulation (e.g., via direct storage write or deposit)
    stdstore.target(address(pool)).sig("feeEarnedInETH()").checked_write(fee);

    // Precondition: getETHBalanceMinusFees() == 0
    assertEq(pool.getETHBalanceMinusFees(), 0);

    // Act: BRIDGER_ROLE calls bridgeAssetsViaNativeBridge
    vm.prank(bridger);
    vm.expectEmit(true, false, false, true);
    emit BridgedETHToL1ViaNativeBridge(l1Vault, 0);   // zero-amount event emitted
    pool.bridgeAssetsViaNativeBridge();

    // Assert: L1Vault received nothing
    assertEq(l1Vault.balance, 0);
}
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L387-389)
```text
    function getETHBalanceMinusFees() public view returns (uint256) {
        return address(this).balance - feeEarnedInETH;
    }
```

**File:** contracts/pools/RSETHPool.sol (L487-493)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/pools/RSETHPool.sol (L556-560)
```text
        uint256 balance = getTokenBalanceMinusFees(token);

        if (balance == 0) {
            revert ZeroBridgeAmount();
        }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L437-443)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L657-663)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/pools/RSETHPoolV2.sol (L292-298)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/interfaces/L2/IL2Messenger.sol (L9-18)
```text
    /// @notice Error thrown when the message value does not match the expected value
    error MismatchedMsgValue();

    /**
     * @notice Bridge ETH from L2 to L1 via a specified bridge contract
     * @param l2bridge The address of the L2 bridge contract
     * @param target The address of the recipient on L1
     * @param value The amount of ETH to send
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable;
```
