### Title
Blacklisted ERC-20 Recipient Permanently Locks Bridged Funds on NEAR — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer()` transfers tokens directly to `payload.recipient` with no fallback. For native ERC-20 tokens that implement address blacklists (USDC, USDT), a blacklisted recipient causes every `finTransfer` call to revert. Because the source-chain tokens are already locked on NEAR and no cancel/refund path is exposed in the EVM contract, the user's funds are permanently irrecoverable.

---

### Finding Description

`finTransfer` in `OmniBridge.sol` handles the final leg of a NEAR→EVM transfer. After verifying the MPC signature and marking the nonce used, it dispatches tokens to `payload.recipient`. For native (non-bridge, non-custom-minter) ERC-20 tokens the dispatch is an unconditional `safeTransfer`: [1](#0-0) 

```solidity
} else {
    IERC20(payload.tokenAddress).safeTransfer(
        payload.recipient,
        payload.amount
    );
}
```

USDC and USDT both maintain on-chain blacklists. If `payload.recipient` is blacklisted at the time `finTransfer` is executed, `safeTransfer` reverts, rolling back the entire transaction (including the nonce mark at line 287). [2](#0-1) 

The nonce is therefore not consumed, so the relayer can retry — but every retry will revert for the same reason. There is no alternative delivery path, no escrow, and no try/catch around the transfer. [3](#0-2) 

On the NEAR side, the user's tokens were locked when `ft_on_transfer` processed the `InitTransferMsg`: [4](#0-3) 

The `pending_transfers` map holds the locked `TransferMessage`, and the MPC-signed payload encodes `recipient` immutably. No public `cancel_transfer` or refund entrypoint is present in the EVM contract, and the NEAR contract has no mechanism to unilaterally release locked tokens when EVM finalization is permanently blocked. [5](#0-4) 

---

### Impact Explanation

**Critical — Irreversible fund lock.**

Once a NEAR→EVM transfer is initiated with a USDC/USDT recipient that is (or becomes) blacklisted:

- The NEAR tokens are locked and cannot be released.
- Every `finTransfer` call on EVM reverts.
- The signed MPC payload is bound to the blacklisted `recipient`; no re-routing is possible without a new MPC signing round, which the protocol does not support for already-pending transfers.
- The user permanently loses access to their bridged value.

This matches the allowed impact: *"Critical. Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge … flows."*

---

### Likelihood Explanation

**Medium.** USDC and USDT are the most commonly bridged stablecoins. Both Centre/Circle and Tether actively blacklist addresses (OFAC sanctions, exchange compliance, exploit response). The window between `initTransfer` on NEAR and `finTransfer` on EVM is non-zero; blacklisting can occur in that window. No attacker action is required — the blacklisting authority (Circle/Tether) acts independently. Any user whose EVM address is sanctioned after initiating a bridge transfer is affected.

---

### Recommendation

Wrap the native ERC-20 transfer in a try/catch and, on failure, credit the amount to an internal escrow mapping keyed by `(tokenAddress, recipient, destinationNonce)`. Expose a separate `claimEscrow` function so the user (or a designated address) can later withdraw. This mirrors the fix applied to the Wagmi Leverage issue.

```solidity
// pseudocode
try IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount) {
    // success
} catch {
    escrow[payload.tokenAddress][payload.recipient] += payload.amount;
    emit EscrowedTransfer(payload.recipient, payload.tokenAddress, payload.amount);
}
```

Additionally, consider adding a `cancelTransfer` path on NEAR that allows the original sender to reclaim locked tokens if the destination-chain finalization has been provably stuck for a configurable timeout.

---

### Proof of Concept

1. Alice holds USDC on NEAR and calls `ft_transfer_call` to the NEAR bridge with `InitTransferMsg { recipient: EVM:0xAlice, token: usdc.near, amount: 1000 }`. Her USDC is locked in `pending_transfers`.
2. The relayer calls `sign_transfer` on NEAR; the MPC signs a `TransferMessagePayload` with `recipient = 0xAlice`.
3. Circle blacklists `0xAlice` (e.g., OFAC compliance) before the relayer submits `finTransfer` on EVM.
4. The relayer calls `OmniBridge.finTransfer(sig, payload)`. Execution reaches line 351: `IERC20(usdc).safeTransfer(0xAlice, 1000)`. USDC reverts because `0xAlice` is blacklisted.
5. The entire transaction reverts. The nonce is not consumed.
6. Every subsequent `finTransfer` attempt reverts identically.
7. Alice's 1000 USDC remain locked in the NEAR bridge contract forever with no recovery path. [1](#0-0) [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L226-247)
```rust
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```

**File:** near/omni-bridge/src/lib.rs (L257-287)
```rust
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
            }
            BridgeOnTransferMsg::FastFinTransfer(fast_fin_transfer_msg) => {
                self.fast_fin_transfer(token_id, amount, signer_id, fast_fin_transfer_msg)
            }
            BridgeOnTransferMsg::UtxoFinTransfer(utxo_fin_transfer_msg) => self.utxo_fin_transfer(
                token_id,
                amount,
                &signer_id,
                &sender_id,
                utxo_fin_transfer_msg,
            ),
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
        };

        promise_or_promise_index_or_value.as_return();
    }
```

**File:** near/omni-bridge/src/lib.rs (L540-557)
```rust
        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

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
