### Title
Unguarded `sendETHToL1ViaBridge` Allows Attacker to Exhaust Linea Bridge Period Limit, Temporarily Blocking Protocol's Native Bridge Path - (File: `contracts/bridges/LineaMessenger.sol`)

---

### Summary

`LineaMessenger.sendETHToL1ViaBridge` has no caller restriction. Any attacker can call it with their own ETH and an arbitrary `target`, consuming the Linea bridge's per-period ETH limit. Once exhausted, the BRIDGER_ROLE's `bridgeAssetsViaNativeBridge` call reverts at the Linea bridge level until the period resets.

---

### Finding Description

`LineaMessenger.sendETHToL1ViaBridge` is `external payable` with only a `nonReentrant` guard — no `onlyRole` or caller whitelist: [1](#0-0) 

The function accepts a caller-supplied `target` and forwards the full `msg.value` to the Linea bridge via `sendMessage`: [2](#0-1) 

The Linea bridge enforces a per-period ETH cap, exposed in the protocol's own interface: [3](#0-2) 

The protocol's legitimate bridge path calls the same `sendETHToL1ViaBridge` through `bridgeAssetsViaNativeBridge`, which is properly role-gated: [4](#0-3) 

An attacker who exhausts `limitInWei()` before BRIDGER_ROLE acts causes `bridgeAssetsViaNativeBridge` to revert at the Linea bridge level for the remainder of the period.

---

### Impact Explanation

The native bridge path is temporarily blocked for the current Linea period. Protocol ETH sitting in the pool cannot be bridged via `bridgeAssetsViaNativeBridge` until the period resets. The attacker's ETH is not stolen from the protocol — the attacker funds the attack themselves. The protocol retains a fallback via LayerZero (`bridgeAssets`), which limits the severity to **Low / Block stuffing**: the native bridge path is disrupted, not all bridging.

---

### Likelihood Explanation

The attack requires the attacker to spend ETH up to the Linea bridge period limit (a real on-chain constraint confirmed by `limitInWei()`). This is a self-funded, costly griefing attack. Block stuffing (to prevent BRIDGER_ROLE from acting within the same period) is not strictly necessary — once the period limit is exhausted, BRIDGER_ROLE's call reverts regardless. Likelihood is **low** due to cost, but the path is fully permissionless and requires no privileged access.

---

### Recommendation

Add an `onlyRole(BRIDGER_ROLE)` modifier to `LineaMessenger.sendETHToL1ViaBridge`, consistent with how all pool-level bridge functions are guarded:

```solidity
// contracts/bridges/LineaMessenger.sol
function sendETHToL1ViaBridge(
    address l2bridge,
    address target,
    uint256 value
) external payable nonReentrant onlyRole(BRIDGER_ROLE) {
```

Alternatively, whitelist the `target` parameter to only allow `l1VaultETHForL2Chain`, preventing misdirected bridging even if the caller restriction is relaxed.

---

### Proof of Concept

```solidity
// Fork test on Linea mainnet
function test_exhaustLineaPeriodLimit() public {
    address lineaBridge = 0x...; // Linea L2MessageService
    address lineaMessenger = address(new LineaMessenger(admin));

    uint256 periodLimit = ILineaMessageService(lineaBridge).limitInWei();
    uint256 currentUsed = ILineaMessageService(lineaBridge).currentPeriodAmountInWei();
    uint256 remaining = periodLimit - currentUsed;

    // Attacker exhausts the remaining period limit with their own ETH
    vm.deal(attacker, remaining + 1 ether);
    vm.prank(attacker);
    LineaMessenger(lineaMessenger).sendETHToL1ViaBridge{value: remaining}(
        lineaBridge,
        attacker, // arbitrary L1 target
        remaining
    );

    // BRIDGER_ROLE's legitimate call now reverts at the Linea bridge level
    vm.prank(bridger);
    vm.expectRevert(); // Linea bridge: period limit exceeded
    pool.bridgeAssetsViaNativeBridge();
}
``` [5](#0-4) [3](#0-2)

### Citations

**File:** contracts/bridges/LineaMessenger.sol (L31-46)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        UtilLib.checkNonZeroAddress(l2bridge);
        UtilLib.checkNonZeroAddress(target);

        if (value == 0) revert ZeroAmount();
        if (msg.value != value) revert MismatchedMsgValue(); // Ensure the sent value matches the expected value to
        // avoid trapping ETH in this contract

        uint256 minimumFee = ILineaMessageService(l2bridge).minimumFeeInWei();
        if (value <= minimumFee) revert InsufficientAmountForBridge(); // Ensure Linea native bridge fee can be covered
        // and there is some ETH actually bridged after deducting the fee

        ILineaMessageService(l2bridge).sendMessage{ value: value }(target, minimumFee, bytes(""));

        emit ETHSentViaLineaBridge(l2bridge, target, value, minimumFee);
    }
```

**File:** contracts/interfaces/L2/ILineaMessageService.sol (L25-37)
```text
     * @notice Returns the ETH bridging limit for the current period in wei
     * @dev This limit is used to control the amount of ETH that can be bridged in a given period
     * @return The limit in wei
     */
    function limitInWei() external view returns (uint256);

    /**
     * @notice Returns the amount of ETH that has already been bridged in the current period
     * @dev The difference between the `limitInWei()` and this amount gives the remaining amount that can be bridged for
     * the current period
     * @return The amount in wei
     */
    function currentPeriodAmountInWei() external view returns (uint256);
```

**File:** contracts/pools/RSETHPool.sol (L481-494)
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
