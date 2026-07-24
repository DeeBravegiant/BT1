### Title
Finalization Record Erasure on `ft_transfer_call` Rejection Enables Cross-Chain Transfer Replay — (File: `near/omni-bridge/src/lib.rs`)

### Summary
When a NEAR-bound `fin_transfer` uses `ft_transfer_call` (triggered by a non-empty `message` field) and the recipient contract rejects all tokens (returns the full amount from `ft_on_transfer`), the bridge calls `remove_fin_transfer`, which **permanently erases the `TransferId` from `finalised_transfers`**. Because the prover proof remains valid on-chain, the same EVM `InitTransfer` event can be re-submitted to `fin_transfer` a second time. If the recipient contract accepts tokens on the second call, the bridge mints tokens again against a single EVM lock/burn, creating unbacked supply.

### Finding Description

`process_fin_transfer_to_near` first marks the transfer as finalised via `add_fin_transfer`, then dispatches tokens via `send_tokens` (which uses `ft_transfer_call` when `msg` is non-empty), then schedules `fin_transfer_send_tokens_callback` as the resolution callback. [1](#0-0) 

Inside `fin_transfer_send_tokens_callback`, when `is_refund_required` returns `true` (i.e., `ft_transfer_call` returned `0`, meaning the receiver rejected all tokens), the code burns the refunded tokens and then calls `remove_fin_transfer`: [2](#0-1) 

`remove_fin_transfer` unconditionally removes the `TransferId` from `finalised_transfers`: [3](#0-2) 

`add_fin_transfer` (the replay guard) only blocks re-entry when the `TransferId` is **present** in the set: [4](#0-3) 

After `remove_fin_transfer` runs, the set no longer contains the `TransferId`. The EVM proof is still valid (the `InitTransfer` event is immutable on-chain), so a second call to `fin_transfer` with the identical `prover_args` passes both `verify_proof` and `add_fin_transfer`, and the bridge mints/unlocks tokens a second time.

### Impact Explanation

An attacker who controls the NEAR recipient contract can selectively reject the first delivery (causing nonce erasure) and accept the second delivery. The EVM side locked or burned tokens exactly once; the NEAR side mints them twice. This produces unbacked wrapped-token supply — a direct violation of the bridge's backing guarantee.

The same structural flaw exists for UTXO-origin transfers: `remove_fin_utxo_transfer` erases entries from `finalised_utxo_transfers` under analogous conditions. [5](#0-4) 

### Likelihood Explanation

The attack requires:
1. The attacker deploys a NEAR contract that rejects the first `ft_transfer_call` (returns full amount from `ft_on_transfer`) and accepts the second.
2. The attacker initiates an EVM `initTransfer` with a non-empty `message` field pointing to that contract.
3. A trusted relayer re-submits the same proof after observing the `FailedFinTransferEvent`.

Step 3 is the key dependency. Automated relayers that retry on apparent failure would naturally re-submit. The attacker does not need any privileged role; they only need to deploy a contract and initiate a standard EVM transfer.

### Recommendation

Do **not** remove the `TransferId` from `finalised_transfers` on delivery failure. A finalised transfer ID must remain permanently marked regardless of whether the downstream token delivery succeeded. Instead:

- Keep the nonce marked as used and emit a `FailedFinTransferEvent` without clearing the record.
- Provide a separate, nonce-preserving refund path (e.g., allow the original sender to reclaim on the source chain by proving the NEAR delivery failed, without re-using the same `TransferId`).

### Proof of Concept

1. Attacker deploys `MaliciousReceiver` on NEAR. Its `ft_on_transfer` returns `amount` (full refund) on the first call and `0` (keep all) on the second call.
2. Attacker calls `initTransfer` on EVM with `message = "trigger"` and `recipient = MaliciousReceiver`.
3. Relayer calls `fin_transfer` on NEAR with the EVM proof.
   - `process_fin_transfer_to_near` → `add_fin_transfer` inserts `TransferId{Eth, nonce=N}` → `ft_transfer_call` → `MaliciousReceiver.ft_on_transfer` returns `amount` → tokens refunded to bridge → `fin_transfer_send_tokens_callback` → `burn_tokens_if_needed` burns refunded tokens → **`remove_fin_transfer` removes `TransferId{Eth, N}`**.
4. Relayer (or attacker acting as relayer) re-submits the identical proof.
   - `add_fin_transfer` succeeds (set no longer contains `TransferId{Eth, N}`) → `ft_transfer_call` → `MaliciousReceiver.ft_on_transfer` returns `0` → tokens kept.
5. `MaliciousReceiver` now holds tokens on NEAR; EVM locked/burned tokens only once → unbacked supply created.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1707-1723)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L1880-1882)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
```

**File:** near/omni-bridge/src/lib.rs (L2231-2239)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```

**File:** near/omni-bridge/src/lib.rs (L2327-2338)
```rust
    fn remove_fin_transfer(&mut self, transfer_id: &TransferId, storage_owner: &AccountId) {
        let storage_usage = env::storage_usage();
        self.finalised_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L2340-2356)
```rust
    fn remove_fin_utxo_transfer(
        &mut self,
        transfer_id: &UnifiedTransferId,
        storage_owner: &AccountId,
    ) {
        let storage_usage = env::storage_usage();

        self.finalised_utxo_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```
