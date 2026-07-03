### Title
Hardcoded `msg.sender` as LayerZero Refund Address in `bridgeAssets()` Can Permanently Block L2→L1 ETH Bridging - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

---

### Summary

In `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV2ExternalBridge.sol`, and `RSETHPoolNoWrapper.sol`, the `bridgeAssets()` function passes `msg.sender` hardcoded as the `_refundAddress` argument to `stargatePool.send()`. If the `BRIDGER_ROLE` holder is a smart contract without a `receive()` or `fallback()` function, LayerZero's attempt to refund any excess native fee to that address will revert, causing the entire `bridgeAssets()` call to revert and permanently blocking L2→L1 ETH bridging.

---

### Finding Description

The `bridgeAssets()` function in all three pool variants constructs a `SendParam` and calls `stargatePool.send()` with `msg.sender` as the third argument (`_refundAddress`):

```solidity
// RSETHPoolV3ExternalBridge.sol L705-706
(MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
    stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```

The `_refundAddress` in the LayerZero/Stargate `send()` interface is the address that receives any excess native fee if the actual fee consumed is less than the `nativeFee` paid:

```solidity
// IStargatePoolNative.sol L58-65
function send(
    SendParam calldata _sendParam,
    MessagingFee calldata _fee,
    address _refundAddress   // <-- receives excess fee refund
) external payable returns (...);
```

The `BRIDGER_ROLE` is commonly assigned to smart contracts (automation bots, keeper contracts, or multisigs without a `receive()` function). When LayerZero attempts to push excess ETH to a smart contract `_refundAddress` that has no `receive()` or `fallback()`, the ETH transfer reverts, which propagates up and causes `stargatePool.send()` — and therefore `bridgeAssets()` — to revert entirely.

The same pattern is present identically in:
- `RSETHPoolV2ExternalBridge.sol` line 522
- `RSETHPoolNoWrapper.sol` line 486

---

### Impact Explanation

If `bridgeAssets()` always reverts because the bridger smart contract cannot receive ETH refunds, all user ETH deposited into the pool is stranded on L2 and cannot be bridged to L1 for restaking. The pool accumulates ETH from user deposits but the `BRIDGER_ROLE` holder is unable to move it to `l1VaultETHForL2Chain`. This constitutes a **temporary (potentially permanent) freezing of user funds** held in the pool.

Impact: **Medium — Temporary freezing of funds.**

---

### Likelihood Explanation

The `BRIDGER_ROLE` is a privileged but non-admin role that is routinely assigned to automation contracts, keeper bots, or custom smart contract wallets. Not all such contracts implement `receive()`. The scenario is realistic whenever the protocol deploys or upgrades its bridger infrastructure to a contract that lacks a native ETH receive hook. Likelihood: **Low**, but the consequence when triggered is a complete halt of L2→L1 bridging.

---

### Recommendation

Accept an explicit `_refundAddress` parameter in `bridgeAssets()` so the caller can specify a safe EOA or a contract known to accept ETH. Alternatively, use `address(this)` as the refund address and add an admin-only ETH withdrawal function to recover any refunded excess fees from the pool contract itself.

```solidity
function bridgeAssets(
    uint256 amount,
    uint256 minAmount,
    uint256 nativeFee,
    address refundAddress   // caller-specified safe refund address
) external payable nonReentrant onlyRole(BRIDGER_ROLE) {
    ...
    stargatePool.send{ value: nativeFee + amount }(sendParam, fee, refundAddress);
}
```

---

### Proof of Concept

1. Admin grants `BRIDGER_ROLE` to an automation contract `BridgerBot` that has no `receive()` function.
2. Users deposit ETH into `RSETHPoolV3ExternalBridge`; the pool accumulates ETH.
3. `BridgerBot` calls `bridgeAssets(amount, minAmount, nativeFee)` with `msg.value = nativeFee`.
4. Inside `stargatePool.send()`, LayerZero computes the actual fee and finds it is 1 wei less than `nativeFee`.
5. LayerZero attempts `BridgerBot.call{value: 1}("")` to refund the excess.
6. `BridgerBot` has no `receive()` — the call reverts.
7. `stargatePool.send()` reverts, `bridgeAssets()` reverts.
8. Every subsequent call to `bridgeAssets()` reverts for the same reason.
9. All user ETH in the pool is frozen on L2 with no path to L1.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L705-706)
```text
        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L521-522)
```text
        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L485-486)
```text
        (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt) =
            stargatePool.send{ value: nativeFee + amount }(sendParam, fee, msg.sender);
```

**File:** contracts/external/layerzero/interfaces/IStargatePoolNative.sol (L54-65)
```text
    /// @param _fee Messaging fee for the LayerZero protocol
    /// @param _refundAddress Address to refund excess fees
    /// @return msgReceipt Receipt of the messaging operation
    /// @return oftReceipt Receipt of the OFT operation
    function send(
        SendParam calldata _sendParam,
        MessagingFee calldata _fee,
        address _refundAddress
    )
        external
        payable
        returns (MessagingReceipt memory msgReceipt, OFTReceipt memory oftReceipt);
```
