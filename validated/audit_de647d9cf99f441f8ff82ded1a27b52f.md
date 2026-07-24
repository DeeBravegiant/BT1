### Title
Native ETH `finTransfer` to Contract Recipients Without `receive`/`fallback` Causes Permanent Fund Lock — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer()` unconditionally executes `payload.recipient.call{value: payload.amount}("")` whenever `payload.tokenAddress == address(0)`, with no guard for `payload.amount == 0`. If `payload.recipient` is a contract that implements neither `receive()` nor `fallback()`, the low-level call reverts for **any** amount value — including zero — causing `FailedToSendEther` to be thrown on every attempt. Because no on-chain recovery path exists in `OmniBridge`, the user's assets locked on the source chain (NEAR) become permanently unclaimable.

---

### Finding Description

In `OmniBridge.finTransfer()`, the native-ETH delivery branch is:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}(
        ""
    );
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

There is no `payload.amount > 0` guard before the call. In the EVM, a `call{value: 0}("")` to a contract that has neither `receive()` nor `fallback()` reverts, because the EVM dispatches empty-calldata calls to `receive()` first, then `fallback()`, and reverts if neither exists — regardless of the ETH value attached.

The nonce is marked consumed **before** the transfer attempt:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [2](#0-1) 

Because the entire transaction reverts on `FailedToSendEther`, the nonce write is also rolled back, so the nonce is not permanently consumed. However, every subsequent relay attempt with the same signed payload will hit the same revert. There is no `recallMessage`, `failMessage`, or equivalent recovery function anywhere in `OmniBridge.sol` that would allow the user to reclaim their locked source-chain assets. [3](#0-2) 

The `TransferMessagePayload` struct places no lower bound on `amount`:

```solidity
struct TransferMessagePayload {
    ...
    uint128 amount;
    address recipient;
    ...
}
``` [4](#0-3) 

---

### Impact Explanation

**Critical — Irreversible fund lock.**

When a user on NEAR initiates a native-ETH bridge transfer specifying a contract address as the EVM recipient, and that contract lacks `receive()`/`fallback()`:

1. NEAR-side assets are locked/burned at `init_transfer` time.
2. Every relay call to `finTransfer` reverts with `FailedToSendEther`.
3. No recovery function exists in `OmniBridge` to redirect or refund the transfer.
4. The user's funds are permanently unclaimable on both chains.

The zero-value sub-case is especially problematic: a user bridging a zero-amount native-ETH message (e.g., a pure message-passing use case) to a contract recipient would not expect the bridge to attempt an ETH push at all, yet the code makes the call unconditionally.

---

### Likelihood Explanation

**Medium.** Contract-to-contract bridge interactions are common in DeFi composability. Any protocol that uses a smart contract wallet, multisig, or custom vault as the EVM recipient — and that contract does not implement `receive()` or `fallback()` — will trigger this path. The zero-value case is reachable whenever the NEAR side permits zero-amount native transfers (no on-chain guard is visible in `finTransfer` itself).

---

### Recommendation

Add a zero-value guard and, for non-zero amounts, consider a pull-based pattern:

```solidity
if (payload.tokenAddress == address(0)) {
    if (payload.amount == 0) {
        // nothing to send; skip the call entirely
    } else {
        (bool success, ) = payload.recipient.call{value: payload.amount}("");
        if (!success) revert FailedToSendEther();
    }
}
```

For the non-zero case, consider storing failed ETH deliveries in a claimable mapping so the recipient can pull funds rather than having the relayer push them, eliminating the permanent-lock risk entirely.

---

### Proof of Concept

1. Deploy a contract `NoFallback` with no `receive()` or `fallback()` on the EVM destination chain.
2. On NEAR, call `init_transfer` specifying `tokenAddress = address(0)` (native ETH), `amount = 0`, and `recipient = address(NoFallback)`.
3. Relay the signed `TransferMessagePayload` to `OmniBridge.finTransfer()`.
4. Observe: `payload.recipient.call{value: 0}("")` reverts → `FailedToSendEther` is thrown.
5. Repeat step 3 with any amount: same result.
6. Observe: no function in `OmniBridge` allows the user to recover the locked NEAR-side assets.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```
