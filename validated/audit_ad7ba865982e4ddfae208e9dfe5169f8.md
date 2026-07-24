### Title
Blacklisted Recipient Causes Permanent Fund Lock With No Recovery Path - (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a cross-chain transfer specifying a recipient address that is subsequently blacklisted by the destination token contract (e.g., USDC), the transfer can never be finalized. Because the recipient is hardcoded in the MPC-signed payload and no cancel/refund mechanism exists, the user's funds are permanently locked in the source-chain bridge contract.

---

### Finding Description

**Transfer initiation** locks or burns tokens on the source chain and records the recipient address in the transfer message. This recipient is then embedded in the MPC-signed `TransferMessagePayload` that drives finalization on the destination chain.

On EVM, `finTransfer` in `OmniBridge.sol` transfers tokens exclusively to `payload.recipient`:

```solidity
IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount);
```

There is no alternative-recipient parameter and no cancel/refund entrypoint. [1](#0-0) 

On Starknet, `fin_transfer` in `omni_bridge.cairo` similarly transfers only to `payload.recipient` with no override path:

```cairo
let success = IERC20Dispatcher { contract_address: payload.token_address }
    .transfer(payload.recipient, payload.amount.into());
assert(success, 'ERR_TRANSFER_FAILED');
``` [2](#0-1) 

On NEAR, `fin_transfer_callback` routes to `process_fin_transfer_to_near` which calls `send_tokens` to the hardcoded `recipient` extracted from the proof, with no user-overridable recipient field. [3](#0-2) 

Because `safeTransfer` / `transfer` to a blacklisted address reverts the entire transaction, the nonce is not consumed and the relayer can retry — but every retry will fail identically. The MPC will only sign the original recipient (it signs the exact `TransferMessagePayload` including `recipient`), so no alternative delivery is possible. [4](#0-3) 

No public cancel-transfer or source-chain refund function exists in any of the in-scope contracts. The only privileged escape hatch is `transfer_token_as_dao` on NEAR, which requires DAO role and is not a user-accessible recovery path. [5](#0-4) 

---

### Impact Explanation

Funds locked on the source chain are permanently unclaimable by the user. For EVM→EVM or EVM→Starknet transfers of USDC (or any token with a blacklist), the full principal is frozen in the bridge contract with no recovery path available to the user. This matches the allowed impact: **"Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge flows."**

---

### Likelihood Explanation

Blacklisting by token contracts such as USDC is a real, documented event (regulatory compliance, sanctions). The window between `initTransfer` and `finTransfer` finalization can span multiple blocks or longer (MPC signing latency, relayer delays), creating a realistic exposure window. Likelihood is low but non-zero, consistent with the external report's medium classification.

---

### Recommendation

1. Add an optional `fallback_recipient` field to `TransferMessagePayload` that the MPC co-signs. If the primary `recipient` transfer fails, retry delivery to `fallback_recipient`.
2. Alternatively, implement a source-chain cancel/refund function that allows the original sender to reclaim locked tokens when a transfer has been pending beyond a timeout threshold and no finalization has succeeded.
3. For the EVM `finTransfer`, consider a try/catch pattern (using a low-level call instead of `safeTransfer`) that, on failure, stores the amount in a claimable mapping keyed by the sender rather than reverting, so the nonce is consumed and the sender can redirect funds.

---

### Proof of Concept

1. User holds USDC on EVM chain A and calls `initTransfer` specifying their EVM chain B address as `recipient`. USDC is locked in `OmniBridge` on chain A. [6](#0-5) 
2. NEAR bridge records the pending transfer with the chain B address as `recipient` in `TransferMessage`. [7](#0-6) 
3. Before finalization, the user's chain B address is blacklisted by the USDC contract on chain B.
4. Relayer calls `finTransfer` on chain B. The MPC signature is valid, but `safeTransfer(payload.recipient, payload.amount)` reverts because `payload.recipient` is blacklisted. The entire transaction reverts; the nonce is not consumed. [8](#0-7) 
5. Every subsequent relay attempt fails identically. The MPC will not sign a different recipient. No cancel/refund function exists.
6. The user's USDC is permanently locked in the chain A `OmniBridge` contract.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-355)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** starknet/src/omni_bridge.cairo (L259-263)
```text
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```

**File:** near/omni-bridge/src/lib.rs (L544-557)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
```

**File:** near/omni-bridge/src/lib.rs (L738-749)
```rust
        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```

**File:** near/omni-bridge/src/lib.rs (L1517-1535)
```rust
    pub fn transfer_token_as_dao(
        &mut self,
        token: AccountId,
        amount: U128,
        recipient: AccountId,
        msg: Option<String>,
    ) -> Promise {
        if let Some(msg) = msg {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_CALL_GAS)
                .ft_transfer_call(recipient, amount, None, msg)
        } else {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        }
    }
```
