### Title
`native_fee` STRK Tokens Permanently Locked in Starknet Bridge Contract — (`starknet/src/omni_bridge.cairo`)

### Summary

The Starknet `init_transfer` function collects `native_fee` STRK tokens from the caller and transfers them into the bridge contract itself. No code path in the Starknet bridge ever forwards these tokens to a relayer or fee recipient. The STRK tokens are permanently locked with no withdrawal or recovery mechanism.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a `native_fee` parameter denominated in STRK tokens:

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
``` [1](#0-0) 

The STRK tokens are transferred to `get_contract_address()` — the bridge contract itself. The `fin_transfer` function on Starknet only mints or transfers `payload.amount` to `payload.recipient`; it performs no transfer of any STRK to `payload.fee_recipient`:

```cairo
fn fin_transfer(ref self: ContractState, signature: Signature, payload: TransferMessagePayload) {
    ...
    if self.is_bridge_token(payload.token_address) {
        IBridgeTokenDispatcher { contract_address: payload.token_address }
            .mint(payload.recipient, payload.amount.into());
    } else {
        let success = IERC20Dispatcher { contract_address: payload.token_address }
            .transfer(payload.recipient, payload.amount.into());
    }
    // fee_recipient is only emitted in the event, never paid
}
``` [2](#0-1) 

The full contract interface — `log_metadata`, `deploy_token`, `fin_transfer`, `init_transfer`, `upgrade_token`, `set_pause_flags`, `pause_all`, `upgrade` — contains no function that withdraws or forwards accumulated STRK `native_fee` tokens. [3](#0-2) 

On the NEAR side, when processing a Starknet-origin transfer, `fin_transfer_send_tokens_callback` mints a STRK-representation token to the fee recipient:

```rust
if transfer_message.fee.native_fee.0 > 0 {
    let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());
    ext_token::ext(native_token_id)
        .with_static_gas(MINT_TOKEN_GAS)
        .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
        .detach();
}
``` [4](#0-3) 

This means: the STRK tokens paid as `native_fee` on Starknet are locked in the bridge contract forever, while NEAR mints STRK-representation tokens to the relayer without any corresponding unlock of the backing STRK on Starknet.

### Impact Explanation

Every call to `init_transfer` with `native_fee > 0` on Starknet causes STRK tokens to be permanently locked in the bridge contract. There is no admin recovery function, no `fin_transfer` path that pays STRK to a fee recipient, and no withdrawal mechanism of any kind. This is an irreversible fund lock affecting all users who pay a native fee on Starknet. Additionally, NEAR mints STRK-representation tokens to relayers without a corresponding Starknet unlock, creating unbacked supply that can never be redeemed back to real STRK.

**Impact category:** Critical — Irreversible fund lock of permanently unclaimable fee value in the bridge fee flow.

### Likelihood Explanation

The `init_transfer` function is a public, permissionless entry point callable by any user. Any user who pays a `native_fee > 0` triggers the lock. The bridge's fee API actively quotes and recommends `native_fee` values to users to incentivize relayers, so this path is exercised in normal protocol operation.

### Recommendation

Add a fee-forwarding step in `fin_transfer` on Starknet that transfers the accumulated STRK `native_fee` to `payload.fee_recipient` when it is set, analogous to how the EVM bridge recovers native fees via `finTransfer` with `tokenAddress=address(0)`. Alternatively, track per-transfer `native_fee` amounts in storage and allow the fee recipient to claim them after a valid `fin_transfer` proof is submitted.

### Proof of Concept

1. User calls `init_transfer(token, amount=1000, fee=10, native_fee=100, ...)` on the Starknet bridge.
2. The bridge executes `transfer_from(caller, get_contract_address(), 100)` — 100 STRK enters the bridge contract.
3. A relayer submits the corresponding proof on NEAR; NEAR mints 100 STRK-representation tokens to the relayer.
4. The 100 STRK on Starknet remain in the bridge contract permanently. No function exists to release them. The relayer's STRK-representation tokens on NEAR have no backing on Starknet. [5](#0-4)

### Citations

**File:** starknet/src/omni_bridge.cairo (L8-32)
```text
#[starknet::interface]
pub trait IOmniBridge<TContractState> {
    fn log_metadata(ref self: TContractState, token: ContractAddress);
    fn deploy_token(ref self: TContractState, signature: Signature, payload: MetadataPayload);
    fn fin_transfer(
        ref self: TContractState, signature: Signature, payload: TransferMessagePayload,
    );
    fn init_transfer(
        ref self: TContractState,
        token_address: ContractAddress,
        amount: u128,
        fee: u128,
        native_fee: u128,
        recipient: ByteArray,
        message: ByteArray,
    );
    fn upgrade_token(
        ref self: TContractState, token_address: ContractAddress, new_class_hash: ClassHash,
    );
    fn set_pause_flags(ref self: TContractState, flags: u8);
    fn pause_all(ref self: TContractState);
    fn get_token_address(self: @TContractState, token_id: ByteArray) -> ContractAddress;
    fn is_bridge_token(self: @TContractState, token_address: ContractAddress) -> bool;
    fn is_transfer_finalised(self: @TContractState, nonce: u64) -> bool;
}
```

**File:** starknet/src/omni_bridge.cairo (L242-279)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::FinTransfer(
                        FinTransfer {
                            origin_chain: payload.origin_chain,
                            origin_nonce: payload.origin_nonce,
                            token_address: payload.token_address,
                            amount: payload.amount,
                            recipient: payload.recipient,
                            fee_recipient: payload.fee_recipient,
                            message: payload.message,
                        },
                    ),
                )
        }
```

**File:** starknet/src/omni_bridge.cairo (L281-331)
```text
        fn init_transfer(
            ref self: ContractState,
            token_address: ContractAddress,
            amount: u128,
            fee: u128,
            native_fee: u128,
            recipient: ByteArray,
            message: ByteArray,
        ) {
            assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');

            assert(amount > 0, 'ERR_ZERO_AMOUNT');
            assert(fee < amount, 'ERR_INVALID_FEE');

            let origin_nonce = self.current_origin_nonce.read() + 1;
            self.current_origin_nonce.write(origin_nonce);

            let caller = get_caller_address();

            if self.is_bridge_token(token_address) {
                IBridgeTokenDispatcher { contract_address: token_address }
                    .burn(caller, amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
                )
        }
```

**File:** near/omni-bridge/src/lib.rs (L1741-1748)
```rust
            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```
