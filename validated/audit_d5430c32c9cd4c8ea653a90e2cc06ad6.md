### Title
Blacklisted Recipient in `finTransfer` Permanently Locks Bridged Funds — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.finTransfer` uses a push-transfer pattern with no fallback or emergency path. For native ERC-20 tokens (e.g., USDC), the transfer is sent directly to `payload.recipient`. If that address is blacklisted by the token contract between the time `initTransfer` is executed on the source chain and `finTransfer` is called on EVM, every relay attempt reverts and the bridged funds become permanently unclaimable.

### Finding Description

In `finTransfer`, after signature verification and nonce marking, the contract unconditionally pushes tokens to `payload.recipient`: [1](#0-0) 

The nonce is marked used before the transfer: [1](#0-0) 

For native (non-bridge, non-custom-minter) ERC-20 tokens the transfer is: [2](#0-1) 

`SafeERC20.safeTransfer` reverts if the token's `transfer` returns false or reverts — which USDC does for blacklisted addresses. Because the entire transaction reverts, the nonce is not consumed, but the transfer can never succeed either: every subsequent relay attempt hits the same revert. There is no pull-pattern fallback, no alternative recipient, and no emergency-processing path to skip the blocked transfer and recover funds.

The same irreversible block applies to the ETH path: [3](#0-2) 

and the ERC-1155 path: [4](#0-3) 

### Impact Explanation

Once `initTransfer` is executed on the source chain, the user's tokens are locked or burned. If the EVM-side `finTransfer` can never succeed (blacklisted recipient, contract rejecting ETH, etc.), those source-chain funds are permanently unclaimable. This matches **Critical — Irreversible fund lock / permanently unclaimable user value**.

### Likelihood Explanation

USDC, USDT, and several other tokens actively maintain blacklists. A recipient address can be blacklisted at any time by the token issuer — including after a bridge transfer is already in flight. No attacker capability is required; the issuer's routine compliance action is sufficient to trigger the lock. The bridge carries no mechanism to detect or recover from this state.

### Recommendation

Implement a pull-pattern for all token deliveries in `finTransfer`:

1. Instead of transferring directly to `payload.recipient`, credit the amount to an internal `claimable[recipient][token]` mapping.
2. Expose a separate `claimTransfer(token, amount)` function that the recipient calls to withdraw.
3. Alternatively, accept an `alternativeRecipient` parameter so a relayer or the user can redirect a stuck transfer to a non-blacklisted address (with appropriate authorization).

This mirrors the fix recommended in the Moloch report and eliminates the permanent-lock path entirely.

### Proof of Concept

1. Alice calls `initTransfer(USDC, 10_000e6, ...)` on the source chain. Her USDC is locked in the source bridge.
2. Before any relayer calls `finTransfer` on EVM, USDC's issuer blacklists Alice's EVM address (e.g., due to a compliance order).
3. Every relayer attempt to call `finTransfer` with `payload.recipient = Alice` hits `safeTransfer → USDC.transfer → revert("Blacklisted")`.
4. The transaction reverts; the nonce is not consumed; but the transfer can never succeed.
5. Alice's 10,000 USDC on the source chain is permanently locked with no recovery path. [5](#0-4)

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
