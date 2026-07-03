### Title
Unguarded `sendETHToL1ViaBridge` in `LineaMessenger` Allows Any Caller to Exhaust Bridge Rate Limit, Temporarily Freezing Pool ETH — (`contracts/bridges/LineaMessenger.sol`)

---

### Summary

`LineaMessenger.sendETHToL1ViaBridge` carries no access control. Any address can call it with arbitrary ETH to consume the Linea bridge's per-period rate limit (`limitInWei`). Once the limit is exhausted, the `BRIDGER_ROLE`'s `bridgeAssetsViaNativeBridge` call reverts, temporarily freezing the pool's ETH until the bridge period resets.

---

### Finding Description

`LineaMessenger` inherits from `Recoverable`, which only adds `DEFAULT_ADMIN_ROLE` gating to token/ETH recovery helpers. The core bridging function is completely open: [1](#0-0) 

There is no `onlyRole`, `onlyPool`, or caller whitelist. Any EOA or contract can supply `msg.value == value` and route ETH through the Linea bridge to any L1 target they choose.

The Linea bridge enforces a rolling per-period ETH cap. The interface explicitly exposes the two values needed to compute remaining capacity: [2](#0-1) 

When `BRIDGER_ROLE` calls `bridgeAssetsViaNativeBridge`, the pool sends the full `ethBalanceMinusFees` through the same messenger: [3](#0-2) [4](#0-3) 

If the remaining capacity has been consumed by a prior call to `sendETHToL1ViaBridge`, the inner `sendMessage` call reverts, bubbling up through `bridgeAssetsViaNativeBridge`.

---

### Impact Explanation

The pool's accumulated ETH cannot be bridged to L1 for the remainder of the current bridge period. Withdrawals and rebalancing that depend on L1 liquidity are delayed. This is a **Medium — Temporary Freezing of Funds**.

---

### Likelihood Explanation

- The attacker's ETH is not lost; it is bridged to an L1 address they control. The only cost is gas and temporary capital lockup.
- No privileged role is required; the function is fully public.
- The attack does not require traditional mempool front-running. The attacker can exhaust capacity at any time before the BRIDGER_ROLE acts.
- **Block stuffing on Linea is not realistic** because Linea uses a centralized sequencer (Consensys-operated). The sequencer controls transaction ordering and there is no exploitable public mempool. The DoS therefore lasts only until the bridge period resets naturally, not indefinitely. This limits severity to Medium rather than High.

---

### Recommendation

Add an access-control guard to `sendETHToL1ViaBridge` in `LineaMessenger`, restricting callers to the pool contract(s) that legitimately use it (e.g., a `CALLER_ROLE` or an immutable `allowedCaller` address set at construction). The other messenger contracts (`ArbitrumMessenger`, `BaseMessenger`, `OptimismMessenger`, `ScrollMessenger`, `UnichainMessenger`) share the same open signature and should be reviewed for the same pattern. [5](#0-4) 

---

### Proof of Concept

```solidity
// Fork test on Linea mainnet (or local fork with Linea bridge mock)
function test_exhaustBridgeCapacity() external {
    ILineaMessageService bridge = ILineaMessageService(LINEA_BRIDGE);
    LineaMessenger messenger = LineaMessenger(MESSENGER_ADDRESS);

    uint256 remaining = bridge.limitInWei() - bridge.currentPeriodAmountInWei();

    // Attacker exhausts remaining capacity; ETH goes to attacker's L1 address
    vm.deal(attacker, remaining);
    vm.prank(attacker);
    messenger.sendETHToL1ViaBridge{value: remaining}(
        LINEA_BRIDGE, attackerL1, remaining
    );

    // BRIDGER_ROLE's call now reverts
    vm.prank(bridger);
    vm.expectRevert(); // Linea bridge: rate limit exceeded
    pool.bridgeAssetsViaNativeBridge();
}
```

### Citations

**File:** contracts/bridges/LineaMessenger.sol (L31-43)
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
```

**File:** contracts/interfaces/L2/ILineaMessageService.sol (L29-37)
```text
    function limitInWei() external view returns (uint256);

    /**
     * @notice Returns the amount of ETH that has already been bridged in the current period
     * @dev The difference between the `limitInWei()` and this amount gives the remaining amount that can be bridged for
     * the current period
     * @return The amount in wei
     */
    function currentPeriodAmountInWei() external view returns (uint256);
```

**File:** contracts/pools/RSETHPool.sol (L481-493)
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
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L651-663)
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
```
