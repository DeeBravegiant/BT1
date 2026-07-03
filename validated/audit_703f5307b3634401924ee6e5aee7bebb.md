### Title
`ScrollMessenger.sendETHToL1ViaBridge` Hardcodes `msg.value == value`, Blocking Fee Payment to Scroll Bridge — (`File: contracts/bridges/ScrollMessenger.sol`)

---

### Summary

`ScrollMessenger.sendETHToL1ViaBridge` enforces `msg.value == value` and then calls `IScrollMessenger.sendMessage{ value: value }`. The Scroll bridge's `sendMessage` is payable and may require `msg.value >= value + fee`. Because the contract enforces an exact match and forwards only the bridged ETH amount, there is no mechanism to include any additional messaging fee. If Scroll introduces or raises a non-zero L2→L1 fee, every call to bridge ETH from Scroll pool contracts to L1 will revert, freezing ETH in the pool.

---

### Finding Description

`ScrollMessenger.sendETHToL1ViaBridge` is the concrete bridge adapter used by Scroll-deployed pool contracts (`RSETHPool`, `RSETHPoolV3ExternalBridge`, etc.) to move ETH from L2 to L1:

```solidity
// contracts/bridges/ScrollMessenger.sol
function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value != value) revert MismatchedMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
}
``` [1](#0-0) 

The Scroll messenger interface declares `sendMessage` as `payable`:

```solidity
function sendMessage(address target, uint256 value, bytes calldata message, uint256 gasLimit) external payable;
``` [2](#0-1) 

On Scroll, `sendMessage` requires `msg.value >= value + fee` where `fee` is the cross-chain messaging fee. Currently, with `gasLimit = 0`, the fee is zero, so `msg.value = value` is sufficient. However:

1. The `MismatchedMsgValue` guard **actively prevents** the caller from ever sending `msg.value > value`, making it impossible to include any fee even if the caller wanted to.
2. If Scroll introduces a non-zero fee, the `sendMessage` call will revert because `msg.value (= value) < value + fee`.

The pool contracts call this path with no room for fees:

```solidity
// contracts/pools/RSETHPool.sol
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
    l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
);
``` [3](#0-2) 

The same pattern appears in `RSETHPoolV3ExternalBridge.sol`: [4](#0-3) 

---

### Impact Explanation

If Scroll introduces a non-zero L2→L1 messaging fee, every call to `bridgeAssetsViaNativeBridge` will revert. ETH accumulated in the Scroll pool contracts cannot be moved to L1. Users' deposited ETH is temporarily frozen in the L2 pool until the contract is upgraded to handle fees. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

Scroll currently charges zero fee for L2→L1 messages when `gasLimit = 0`. However, Scroll's fee model is controlled by the bridge operators and can be changed via governance at any time. The `MismatchedMsgValue` guard makes the contract structurally incapable of adapting without an upgrade. Likelihood is **Low** (fee is currently zero, but the structural inability to pay fees is a latent risk).

---

### Recommendation

Remove the strict equality check and allow `msg.value >= value` so callers can include an additional fee. Forward the full `msg.value` to `sendMessage`, not just `value`:

```solidity
function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
    if (msg.value < value) revert InsufficientMsgValue();
    IScrollMessenger(l2bridge).sendMessage{ value: msg.value }(target, value, "", 0, msg.sender);
}
```

Pool callers (`bridgeAssetsViaNativeBridge`) should also be updated to accept and forward an explicit fee parameter alongside the bridged amount.

---

### Proof of Concept

1. Scroll governance sets a non-zero L2→L1 messaging fee (e.g., 0.001 ETH).
2. `BRIDGER_ROLE` calls `RSETHPool.bridgeAssetsViaNativeBridge()` on Scroll.
3. The pool calls `ScrollMessenger.sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(...)`.
4. `ScrollMessenger` enforces `msg.value == value` — the caller cannot include extra ETH for the fee.
5. `IScrollMessenger.sendMessage{ value: value }` is called, but Scroll requires `msg.value >= value + fee`.
6. The Scroll bridge reverts. ETH remains locked in the pool contract on Scroll L2.
7. All subsequent bridge attempts fail identically until the contract is upgraded.

### Citations

**File:** contracts/bridges/ScrollMessenger.sol (L21-24)
```text
    function sendETHToL1ViaBridge(address l2bridge, address target, uint256 value) external payable nonReentrant {
        if (msg.value != value) revert MismatchedMsgValue();
        IScrollMessenger(l2bridge).sendMessage{ value: value }(target, value, "", 0, msg.sender);
    }
```

**File:** contracts/interfaces/L2/IScrollMessenger.sol (L63-63)
```text
    function sendMessage(address target, uint256 value, bytes calldata message, uint256 gasLimit) external payable;
```

**File:** contracts/pools/RSETHPool.sol (L489-491)
```text
        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L659-661)
```text
        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );
```
