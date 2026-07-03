### Title
Unrestricted `sendETHToL1ViaBridge` Allows Any Caller to Exhaust Linea Bridge Period Limit, Temporarily Freezing Pool ETH - (`contracts/bridges/LineaMessenger.sol`)

---

### Summary

`LineaMessenger.sendETHToL1ViaBridge` has no caller restriction and accepts a caller-supplied `l2bridge` address. Any unprivileged attacker can call it with the real Linea bridge address and their own ETH, consuming the bridge's global `limitInWei` period quota. Once exhausted, the pool's `bridgeAssetsViaNativeBridge` call reverts, freezing all accumulated pool ETH until the next period resets.

---

### Finding Description

`LineaMessenger.sendETHToL1ViaBridge` is declared `external payable` with only a `nonReentrant` guard and no role check or caller whitelist: [1](#0-0) 

The function accepts an arbitrary `l2bridge` address and forwards the caller's ETH directly to `ILineaMessageService(l2bridge).sendMessage`. The Linea bridge enforces a **global** `limitInWei` per period: [2](#0-1) 

Because the limit is global (not per-sender), any call to `sendMessage` — regardless of who initiates it — counts against the shared quota. An attacker who calls `sendETHToL1ViaBridge` with `value = limitInWei - currentPeriodAmountInWei` exhausts the remaining quota for the current period. The attacker's ETH is bridged to their own L1 address, so they recover principal (minus the `minimumFee`), making the attack economically sustainable.

After exhaustion, when the BRIDGER_ROLE calls `bridgeAssetsViaNativeBridge` on the pool (e.g., `RSETHPoolV2ExternalBridge`): [3](#0-2) 

...the inner `sendMessage` call reverts with a period-limit error. The pool's ETH remains locked in the contract until the Linea bridge period resets. The attacker can repeat this at every period reset, sustaining the freeze indefinitely.

The same pattern applies to `RSETHPoolV2.bridgeAssets` and `RSETHPoolV3ExternalBridge.bridgeAssetsViaNativeBridge`, all of which route through `LineaMessenger`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Medium. Temporary freezing of funds** (escalating toward permanent if sustained).

All ETH accumulated in the Linea pool(s) is frozen for the duration of each Linea bridge rate-limit period. Users who deposited ETH expecting it to be bridged to L1 experience delayed settlement. If the attacker repeats the attack at every period reset, the freeze becomes effectively permanent at low cost (only `minimumFee` per period is lost; principal is recovered on L1).

---

### Likelihood Explanation

High. The preconditions are:
1. `sendETHToL1ViaBridge` is publicly callable — confirmed, no access control.
2. The attacker needs ETH equal to the remaining period quota — recoverable on L1 minus fees.
3. No front-running or privileged access required.

The attack is a single transaction and is repeatable every period.

---

### Recommendation

Restrict `sendETHToL1ViaBridge` to only be callable by the authorized pool contract(s). The simplest fix is to add an access control check, for example using the inherited `Recoverable` admin pattern or an explicit allowlist:

```solidity
// Option A: restrict to a single authorized caller set at construction
address public authorizedCaller;

modifier onlyAuthorizedCaller() {
    if (msg.sender != authorizedCaller) revert Unauthorized();
    _;
}

function sendETHToL1ViaBridge(...) external payable nonReentrant onlyAuthorizedCaller { ... }
```

Alternatively, store the canonical `l2bridge` address in the contract at deployment and remove it as a caller-supplied parameter, so the attacker cannot route arbitrary ETH through the contract at all.

---

### Proof of Concept

```solidity
// Fork Linea mainnet
// LineaMessenger deployed at 0x838686d23521435B528F68Cde6404C07ae007299
// Linea bridge at LINEA_BRIDGE

ILineaMessageService bridge = ILineaMessageService(LINEA_BRIDGE);
LineaMessenger messenger = LineaMessenger(0x838686d23521435B528F68Cde6404C07ae007299);

uint256 remaining = bridge.limitInWei() - bridge.currentPeriodAmountInWei();

// Step 1: attacker exhausts the period limit using their own ETH
messenger.sendETHToL1ViaBridge{value: remaining}(LINEA_BRIDGE, attacker, remaining);

// Step 2: assert period limit is now exhausted
assertEq(bridge.limitInWei() - bridge.currentPeriodAmountInWei(), 0);

// Step 3: BRIDGER_ROLE attempts to bridge pool ETH — reverts
vm.prank(BRIDGER_ROLE);
vm.expectRevert(); // Linea bridge: RateLimitExceeded or equivalent
RSETHPoolV2ExternalBridge(LINEA_POOL).bridgeAssetsViaNativeBridge(poolBalance);
// Pool ETH is now frozen until next period
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

**File:** contracts/pools/RSETHPoolV2.sol (L286-299)
```text
    function bridgeAssets() external nonReentrant onlyRole(BRIDGER_ROLE) {
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L651-664)
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
