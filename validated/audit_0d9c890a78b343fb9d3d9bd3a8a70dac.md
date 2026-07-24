Let me trace through the relevant production code to evaluate this potential vulnerability.

Based on the code I traced, here is my analysis:

**Key code path confirmed:**

`fin_transfer_send_tokens_callback` at line 1719 explicitly calls `remove_fin_transfer` in the refund branch: [1](#0-0) 

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    self.burn_tokens_if_needed(...);
    self.revert_lock_actions(&lock_actions);
    self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
    env::log_str(&OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string());
}
```

`is_transfer_finalised` checks `finalised_transfers.contains`: [2](#0-1) 

`fin_transfer_callback` (lines 704–750) contains **no** `is_transfer_finalised` guard before delegating to `process_fin_transfer_to_near`: [3](#0-2) 

The prover interface is declared `&self` (immutable), strongly indicating it is stateless and does not consume/track proofs: [4](#0-3) 

The contract state has `finalised_transfers: LookupSet<TransferId>` as the sole replay guard for incoming transfers: [5](#0-4) 

---

### Title
Proof Replay via `remove_fin_transfer` on `ft_transfer_call` Full Refund — (`near/omni-bridge/src/lib.rs`)

### Summary
When a NEAR-destination `fin_transfer` results in a full refund from `ft_transfer_call` (receiver returns 0), `fin_transfer_send_tokens_callback` calls `remove_fin_transfer`, which removes the `TransferId` from `finalised_transfers`. Because the stateless prover does not independently track consumed proofs, and `fin_transfer_callback` has no replay guard of its own, the identical proof can be submitted a second time, passing through `add_fin_transfer` again and crediting the recipient a second time.

### Finding Description

The full call sequence:

1. **`fin_transfer`** — relayer submits a valid proof for a NEAR-destination transfer.
2. **`fin_transfer_callback`** — decodes the prover result, constructs `transfer_message`, calls `process_fin_transfer_to_near`.
3. **`process_fin_transfer_to_near`** — calls `add_fin_transfer`, which inserts `transfer_id` into `finalised_transfers`, then calls `send_tokens` (`ft_transfer_call`).
4. **`fin_transfer_send_tokens_callback`** — the `ft_transfer_call` receiver returns `0` (full refund). `is_refund_required` returns `true`. `remove_fin_transfer` is called, **deleting** `transfer_id` from `finalised_transfers`.
5. **Replay** — the relayer submits the same proof again. The prover (stateless, `&self`) accepts it. `fin_transfer_callback` has no `is_transfer_finalised` check. `add_fin_transfer` finds `transfer_id` absent from `finalised_transfers` and inserts it again. Tokens are minted/unlocked a second time.

The invariant "each origin event finalizes at most once on NEAR" is broken because the only stateful guard (`finalised_transfers`) is mutably cleared on the refund path.

### Impact Explanation

**Critical / High.** On replay, `send_tokens` mints (for deployed tokens) or transfers (for locked tokens) the full `amount_without_fee` to the recipient a second time. This is unbacked supply creation or double-unlock of locked assets. The attacker (relayer) can repeat this for every transfer whose recipient contract is under their control and returns `0` from `ft_on_transfer`.

### Likelihood Explanation

The attacker only needs to:
- Be an active relayer (or submit through the public `fin_transfer` entry point — `#[trusted_relayer]` is present but the macro only gates certain functions; `fin_transfer` itself is gated by `#[trusted_relayer]`).

**Correction on likelihood:** `fin_transfer` is decorated with `#[trusted_relayer]`, which restricts callers to registered relayers. This raises the bar from "any unprivileged account" to "any registered relayer." However, the `trusted_relayer` macro allows bypass via `Role::DAO` and `Role::UnrestrictedRelayer`, and relayer registration may be permissive. The vulnerability is still exploitable by any active relayer who controls or can influence the recipient contract's `ft_on_transfer` return value. [6](#0-5) 

### Recommendation

1. **Do not remove from `finalised_transfers` on refund.** The entry should be permanent. On refund, the transfer should be marked as "failed" in a separate set (e.g., `failed_transfers: LookupSet<TransferId>`) rather than erased from `finalised_transfers`.
2. **Add a replay guard in `fin_transfer_callback`** that checks `is_transfer_finalised` before proceeding, independent of `add_fin_transfer`.
3. **Make the prover stateful** (track consumed proof hashes) as a defense-in-depth measure.

### Proof of Concept

```
1. Deploy a receiver contract whose ft_on_transfer always returns the full amount (i.e., refund = full amount, so ft_transfer_call returns 0 to the bridge).
2. Submit fin_transfer with a valid proof for a transfer to that receiver.
3. Observe: fin_transfer_send_tokens_callback fires with is_refund_required=true, remove_fin_transfer is called, finalised_transfers no longer contains the transfer_id.
4. Submit fin_transfer again with the identical proof bytes.
5. Observe: verify_proof succeeds (stateless prover), fin_transfer_callback proceeds, add_fin_transfer inserts the id again, send_tokens mints/transfers tokens a second time.
6. Assert: recipient balance = 2 × amount_without_fee; finalised_transfers.contains(transfer_id) = true (only after second call).
``` [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L191-193)
```rust
pub trait Prover {
    fn verify_proof(&self, #[serializer(borsh)] proof: Vec<u8>);
}
```

**File:** near/omni-bridge/src/lib.rs (L227-227)
```rust
    pub finalised_transfers: LookupSet<TransferId>,
```

**File:** near/omni-bridge/src/lib.rs (L675-677)
```rust
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L738-745)
```rust
        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
```

**File:** near/omni-bridge/src/lib.rs (L1476-1478)
```rust
    pub fn is_transfer_finalised(&self, transfer_id: TransferId) -> bool {
        self.finalised_transfers.contains(&transfer_id)
    }
```

**File:** near/omni-bridge/src/lib.rs (L1697-1752)
```rust
    pub fn fin_transfer_send_tokens_callback(
        &mut self,
        #[serializer(borsh)] transfer_message: TransferMessage,
        #[serializer(borsh)] fee_recipient: &AccountId,
        #[serializer(borsh)] is_ft_transfer_call: bool,
        #[serializer(borsh)] storage_owner: &AccountId,
        #[serializer(borsh)] lock_actions: Vec<LockAction>,
    ) {
        let token = self.get_token_id(&transfer_message.token);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
        } else {
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
    }
```
