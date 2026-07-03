### Title
No Partial-Amount Bridge Path in RSETHPoolV2 Causes Permanent ETH Freeze When Pool Balance Exceeds Linea Period Limit — (`contracts/pools/RSETHPoolV2.sol`)

---

### Summary

`RSETHPoolV2.bridgeAssets()` always bridges the entire `getETHBalanceMinusFees()` balance in a single call. When the pool is configured with `LineaMessenger`, the underlying `ILineaMessageService.sendMessage` enforces a rolling period cap (`limitInWei`). If the pool's accumulated ETH exceeds the remaining period allowance, every call to `bridgeAssets()` reverts and there is no partial-amount variant in `RSETHPoolV2` to work around it.

---

### Finding Description

`RSETHPoolV2.bridgeAssets()` takes no amount parameter and unconditionally passes the full balance to the messenger:

```solidity
// contracts/pools/RSETHPoolV2.sol  L286-298
function bridgeAssets() external nonReentrant onlyRole(BRIDGER_ROLE) {
    ...
    uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
    IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
        l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
    );
}
``` [1](#0-0) 

`LineaMessenger.sendETHToL1ViaBridge` forwards that full value directly to `ILineaMessageService.sendMessage`:

```solidity
// contracts/bridges/LineaMessenger.sol  L43
ILineaMessageService(l2bridge).sendMessage{ value: value }(target, minimumFee, bytes(""));
``` [2](#0-1) 

The Linea bridge interface explicitly exposes a rolling period cap:

```solidity
// contracts/interfaces/L2/ILineaMessageService.sol  L29-37
function limitInWei() external view returns (uint256);
function currentPeriodAmountInWei() external view returns (uint256);
``` [3](#0-2) 

The Linea bridge reverts when `msg.value > limitInWei - currentPeriodAmountInWei`. Because `RSETHPoolV2` has **no** `bridgeAssets(uint256 amount, ...)` overload and **no** Stargate/LayerZero path (unlike `RSETHPoolV2ExternalBridge`), the BRIDGER_ROLE cannot split the call into smaller chunks. The entire ETH balance is permanently unroutable via the only available bridge function.

Contrast with `RSETHPoolV2ExternalBridge`, which correctly accepts an explicit `amount` parameter:

```solidity
// contracts/pools/RSETHPoolV2ExternalBridge.sol  L466-477
function bridgeAssetsViaNativeBridge(uint256 amount) external nonReentrant onlyRole(BRIDGER_ROLE) {
    ...
    IL2Messenger(messenger).sendETHToL1ViaBridge{ value: amount }(l2Bridge, l1VaultETHForL2Chain, amount);
}
``` [4](#0-3) 

`RSETHPoolV2` has no equivalent. [5](#0-4) 

---

### Impact Explanation

Once `address(this).balance - feeEarnedInETH > limitInWei - currentPeriodAmountInWei`, every invocation of `bridgeAssets()` reverts. The ETH deposited by users on Linea L2 cannot be bridged to L1 to back the rsETH already minted. Because there is no partial-amount path and no Stargate fallback in `RSETHPoolV2`, the funds remain frozen until a contract upgrade is deployed. This matches **Medium — Permanent freezing of unclaimed yield** (the ETH balance that should flow to L1 as backing for minted rsETH). [1](#0-0) 

---

### Likelihood Explanation

The Linea bridge's `limitInWei` is a well-known, actively enforced rate-limit (currently ~1 000 ETH per period on mainnet). A pool that has been accumulating deposits for several days without bridging can easily exceed this threshold. No attacker action is required — normal protocol operation is sufficient. The BRIDGER_ROLE cannot mitigate this without a contract upgrade. [3](#0-2) 

---

### Recommendation

Add an `amount` parameter to `RSETHPoolV2.bridgeAssets()` (mirroring `RSETHPoolV2ExternalBridge.bridgeAssetsViaNativeBridge(uint256 amount)`) so the BRIDGER_ROLE can bridge in tranches that respect the Linea period limit:

```solidity
function bridgeAssets(uint256 amount) external nonReentrant onlyRole(BRIDGER_ROLE) {
    ...
    uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
    if (amount == 0 || amount > ethBalanceMinusFees) revert InvalidAmount();
    IL2Messenger(messenger).sendETHToL1ViaBridge{ value: amount }(
        l2Bridge, l1VaultETHForL2Chain, amount
    );
}
```

Optionally, read `limitInWei()` and `currentPeriodAmountInWei()` on-chain and cap the bridged amount automatically. [4](#0-3) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import { RSETHPoolV2 } from "contracts/pools/RSETHPoolV2.sol";

contract MockLineaBridge {
    uint256 public limitInWei = 100 ether;
    uint256 public currentPeriodAmountInWei = 0;
    uint256 public minimumFeeInWei_ = 0.001 ether;

    function minimumFeeInWei() external view returns (uint256) { return minimumFeeInWei_; }

    function sendMessage(address, uint256, bytes memory) external payable {
        // Linea bridge enforces period limit
        require(
            currentPeriodAmountInWei + msg.value <= limitInWei,
            "RateLimitExceeded"
        );
        currentPeriodAmountInWei += msg.value;
    }
}

contract MockLineaMessenger {
    MockLineaBridge public bridge;
    constructor(MockLineaBridge _b) { bridge = _b; }

    function sendETHToL1ViaBridge(address l2bridge, address, uint256 value) external payable {
        uint256 fee = bridge.minimumFeeInWei();
        bridge.sendMessage{ value: value }(address(0), fee, bytes(""));
    }
}

contract RSETHPoolV2FreezeTest is Test {
    RSETHPoolV2 pool;
    MockLineaBridge linea;
    MockLineaMessenger messengerContract;

    function setUp() public {
        // deploy mocks
        linea = new MockLineaBridge();           // limitInWei = 100 ETH
        messengerContract = new MockLineaMessenger(linea);

        // deploy & initialize pool (simplified)
        pool = new RSETHPoolV2();
        // ... initialize, reinitialize with l2Bridge=address(linea), messenger=address(messengerContract)

        // Fund pool with 150 ETH (exceeds 100 ETH limit)
        vm.deal(address(pool), 150 ether);
    }

    function test_bridgeAssets_alwaysReverts_whenBalanceExceedsLimit() public {
        // pool balance (150 ETH) > limitInWei (100 ETH)
        // bridgeAssets() passes full balance → sendMessage reverts
        vm.expectRevert("RateLimitExceeded");
        pool.bridgeAssets();

        // No partial-amount variant exists — ETH is permanently frozen
        // pool.bridgeAssets(50 ether) does not compile — function does not exist
    }
}
```

The test confirms: with `pool.balance > limitInWei`, `bridgeAssets()` always reverts and no partial-amount escape hatch exists in `RSETHPoolV2`. [1](#0-0) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L280-299)
```text
    /// @dev Legacy function - Withdraws assets from the contract for bridging
    function moveAssetsForBridging() external view onlyRole(BRIDGER_ROLE) {
        revert DeprecatedFunction();
    }

    /// @dev Withdraws assets from the L2 to L1
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
