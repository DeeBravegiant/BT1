### Title
`LineaMessenger` Ignores Bridge Period Limits, Causing Temporary Freezing of Unclaimed Yield — (`contracts/bridges/LineaMessenger.sol`)

### Summary

`LineaMessenger.sendETHToL1ViaBridge` never reads `limitInWei()` or `currentPeriodAmountInWei()` from the Linea bridge before calling `sendMessage`. When the pool's full ETH balance exceeds the bridge's remaining period capacity, `sendMessage` reverts inside the bridge, the entire `bridgeAssets()` call reverts, and no partial-amount fallback exists. Yield is frozen until the next period resets and the BRIDGER_ROLE retries.

### Finding Description

`RSETHPoolV2.bridgeAssets()` always bridges the full `ethBalanceMinusFees` with no amount parameter: [1](#0-0) 

This calls `LineaMessenger.sendETHToL1ViaBridge`, which reads `minimumFeeInWei()` but completely ignores the two period-limit functions that the protocol's own interface explicitly exposes: [2](#0-1) 

The interface defines `limitInWei()` and `currentPeriodAmountInWei()` precisely to allow callers to compute remaining capacity before sending: [3](#0-2) 

`LineaMessenger` uses `ILineaMessageService` but only calls `minimumFeeInWei()`, leaving the period-limit check entirely unimplemented. There is no partial-send path anywhere in the call chain — `bridgeAssets()` takes no `amount` argument and `sendETHToL1ViaBridge` forwards the full `value` unconditionally.

### Impact Explanation

When `ethBalanceMinusFees > (limitInWei() - currentPeriodAmountInWei())`, the Linea bridge's `sendMessage` reverts. This bubbles up through `LineaMessenger` and `RSETHPoolV2.bridgeAssets()`, reverting the entire transaction. All accumulated pool yield remains on L2 until the bridge period resets and the BRIDGER_ROLE retries. The impact is **temporary freezing of unclaimed yield** (one full bridge period per occurrence). The "permanent" framing in the question is overstated — the period resets and the bridger can retry — but the temporary freeze is concrete and real.

### Likelihood Explanation

The Linea bridge enforces a rolling ETH period limit. If other protocol users or unrelated bridge traffic partially consumes the period limit, and the pool has accumulated enough ETH yield to exceed the remainder, every `bridgeAssets()` call will revert for the rest of that period. This is a realistic operational condition, not a theoretical edge case, especially as pool TVL grows.

### Recommendation

Before calling `sendMessage`, `LineaMessenger.sendETHToL1ViaBridge` should compute the remaining capacity and cap the bridged amount:

```solidity
uint256 remaining = ILineaMessageService(l2bridge).limitInWei()
    - ILineaMessageService(l2bridge).currentPeriodAmountInWei();
uint256 amountToBridge = value > remaining ? remaining : value;
```

Alternatively, `RSETHPoolV2.bridgeAssets()` should accept an explicit `amount` parameter (as `RSETHPoolV2ExternalBridge.bridgeAssetsViaNativeBridge(uint256 amount)` already does) so the BRIDGER_ROLE can pass a pre-computed safe amount off-chain. [4](#0-3) 

### Proof of Concept

1. Deploy a mock `ILineaMessageService` where `limitInWei()` returns 10 ETH, `currentPeriodAmountInWei()` returns 9 ETH (1 ETH remaining), and `sendMessage` reverts when `msg.value > 1 ETH`.
2. Fund `RSETHPoolV2` with 2 ETH of yield (`feeEarnedInETH = 0`, `address(this).balance = 2 ETH`).
3. Call `RSETHPoolV2.bridgeAssets()` as BRIDGER_ROLE.
4. Observe: `getETHBalanceMinusFees()` returns 2 ETH → `LineaMessenger.sendETHToL1ViaBridge` forwards 2 ETH → mock `sendMessage` reverts → entire `bridgeAssets()` reverts.
5. Assert: pool ETH balance unchanged; no partial bridge occurred; no fallback path exists.

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L292-296)
```text
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/bridges/LineaMessenger.sol (L39-43)
```text
        uint256 minimumFee = ILineaMessageService(l2bridge).minimumFeeInWei();
        if (value <= minimumFee) revert InsufficientAmountForBridge(); // Ensure Linea native bridge fee can be covered
        // and there is some ETH actually bridged after deducting the fee

        ILineaMessageService(l2bridge).sendMessage{ value: value }(target, minimumFee, bytes(""));
```

**File:** contracts/interfaces/L2/ILineaMessageService.sol (L24-37)
```text
    /**
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L466-479)
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
```
