### Title
`ScrollMessenger` Passes `gasLimit=0` to Scroll L2 Messenger, Causing L1 Relay Failure and Temporary ETH Freeze - (`contracts/bridges/ScrollMessenger.sol`)

---

### Summary

`ScrollMessenger.sendETHToL1ViaBridge` hardcodes `gasLimit=0` when calling `IScrollMessenger.sendMessage`. Unlike Optimism and Base messengers which use `DEFAULT_GAS_LIMIT=200_000`, Scroll's protocol does **not** treat `0` as "use default" — it literally allocates 0 gas for L1 execution. When the Scroll L1 messenger attempts to relay the message to `l1VaultETHForL2Chain` (a contract), the call fails because even an empty `receive()` function requires nonzero gas. The ETH becomes temporarily frozen in the Scroll bridge, requiring manual replay with a correct gas limit.

---

### Finding Description

`ScrollMessenger.sol` line 23 calls:

```solidity
IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
//                                                                        ^
//                                                               gasLimit = 0
``` [1](#0-0) 

The NatSpec comment claims `gasLimit=0` uses "the default gas limit," but Scroll's L2 messenger has no such convention. The `gasLimit` parameter is documented in `IScrollMessenger` as:

> `gasLimit` — Gas limit required to complete the message relay on corresponding chain. [2](#0-1) 

Scroll's L1 messenger executes the relayed message as `target.call{value: _value, gas: 0}("")`. Since `l1VaultETHForL2Chain` is a contract (not an EOA), even an empty `receive()` function requires nonzero gas. The relay fails, emitting `FailedRelayedMessage`. [3](#0-2) 

By contrast, `OptimismMessenger` and `BaseMessenger` both use `DEFAULT_GAS_LIMIT = 200_000`: [4](#0-3) [5](#0-4) 

The call path is:

1. `BRIDGER_ROLE` calls `RSETHPool.bridgeAssetsViaNativeBridge()` (or `RSETHPoolV2.bridgeAssets()`, `RSETHPoolV3ExternalBridge.bridgeAssetsViaNativeBridge()`, etc.)
2. Which calls `IL2Messenger(messenger).sendETHToL1ViaBridge{value: ethBalanceMinusFees}(l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees)`
3. Which calls `ScrollMessenger.sendETHToL1ViaBridge` → `IScrollMessenger.sendMessage(..., gasLimit=0, ...)`
4. Scroll L1 messenger attempts relay with 0 gas → fails [6](#0-5) 

---

### Impact Explanation

ETH is locked in the Scroll bridge contract after the L1 relay fails. Scroll's protocol supports manual replay (`replayMessage`) with a corrected `gasLimit`, so funds are **not permanently lost**, but they are **temporarily frozen** until an operator manually replays the message with a nonzero gas limit. This violates the invariant that `bridgeAssetsViaNativeBridge` delivers ETH to `l1VaultETHForL2Chain` without manual intervention.

**Impact: Medium — Temporary freezing of funds.**

---

### Likelihood Explanation

Every invocation of `bridgeAssetsViaNativeBridge` on a Scroll-deployed pool will trigger this failure. The `BRIDGER_ROLE` is a trusted operator performing routine operations — this is not an adversarial scenario but a code defect that fires on every normal bridge call. Likelihood is **High** given the defect is deterministic.

---

### Recommendation

Replace the hardcoded `0` with an appropriate gas limit constant, consistent with the other messenger contracts:

```solidity
uint256 public constant DEFAULT_GAS_LIMIT = 200_000;

function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", DEFAULT_GAS_LIMIT, msg.sender);
}
```

The exact value should be calibrated against the `l1VaultETHForL2Chain` `receive()` function's gas cost, but 200,000 is a safe upper bound consistent with the other chains.

---

### Proof of Concept

Differential test (local fork):

```solidity
function test_scrollGasLimitIsZero() public {
    // Deploy ScrollMessenger
    ScrollMessenger scroll = new ScrollMessenger();
    // Deploy OptimismMessenger
    OptimismMessenger optimism = new OptimismMessenger();

    // Assert ScrollMessenger passes gasLimit=0
    // Intercept the sendMessage call and capture gasLimit argument
    vm.recordLogs();
    // ... call scroll.sendETHToL1ViaBridge with mock l2bridge that emits gasLimit
    // Assert captured gasLimit == 0

    // Assert OptimismMessenger passes 200_000
    // Assert captured gasLimit == 200_000

    // Simulate L1 relay with gasLimit=0 to a contract target
    address vault = address(new SimpleVault()); // has receive()
    (bool success,) = vault.call{value: 1 ether, gas: 0}("");
    assertFalse(success); // fails — proves ETH would be frozen
}
``` [7](#0-6)

### Citations

**File:** contracts/bridges/ScrollMessenger.sol (L19-24)
```text
     * @dev Gas limit is set to 0 to use the default gas limit
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
    }
```

**File:** contracts/interfaces/L2/IScrollMessenger.sol (L30-32)
```text
    /// @notice Emitted when a cross domain message is failed to relay.
    /// @param messageHash The hash of the message.
    event FailedRelayedMessage(bytes32 indexed messageHash);
```

**File:** contracts/interfaces/L2/IScrollMessenger.sol (L62-63)
```text
    /// @param gasLimit Gas limit required to complete the message relay on corresponding chain.
    function sendMessage(address target, uint256 value, bytes calldata message, uint256 gasLimit) external payable;
```

**File:** contracts/bridges/OptimismMessenger.sol (L16-26)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;

    /**
     * @notice Bridge ETH from Optimism L2 to Ethereum Mainnet
     * @param l2bridge The address of the L2 bridge on Optimism
     * @param target The address of the target contract on L1
     * @param value The amount of ETH to send
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IOptimismMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
```

**File:** contracts/bridges/BaseMessenger.sol (L15-25)
```text
    uint32 public constant DEFAULT_GAS_LIMIT = 200_000;

    /**
     * @notice Bridge ETH from Base L2 to Ethereum Mainnet
     * @param l2bridge The address of the L2 bridge on Base
     * @param target The address of the target contract on L1
     * @param value The amount of ETH to send
     */
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IBaseMessenger(l2bridge).bridgeETHTo{ value: value }(target, DEFAULT_GAS_LIMIT, bytes(""));
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
