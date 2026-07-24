Now I have enough context to analyze this fully. Let me check the `fast_fin_transfer_to_near_callback` for any finalisation guard and the `send_tokens` behavior for bridged tokens.

### Title
Race Between `fast_fin_transfer_to_near_callback` and `fin_transfer_callback` Enables Double-Credit for Bridged Tokens — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`fast_fin_transfer_to_near_callback` never checks `finalised_transfers` before minting tokens. Because the fast-transfer entry-point (`fast_fin_transfer`) only checks `is_unified_transfer_finalised` at call time — before the callback is even scheduled — a concurrent `fin_transfer_callback` that runs in the window between those two events will find `fast_transfer_status = None`, mint tokens to the original recipient, and mark the transfer finalised. When the already-scheduled `fast_fin_transfer_to_near_callback` later executes, it sees an empty `fast_transfers` map, inserts a new entry, and mints the same amount again. For bridged (deployed) tokens, both mints succeed unconditionally, producing one extra unbacked token unit per exploited transfer.

---

### Finding Description

**Two independent state sets guard the two flows:**

| Set | Written by | Read by |
|---|---|---|
| `finalised_transfers` | `add_fin_transfer` (in `process_fin_transfer_to_near`) | `is_unified_transfer_finalised` (only called from `fast_fin_transfer` entry-point) |
| `fast_transfers` | `add_fast_transfer` (in `fast_fin_transfer_to_near_callback`) | `get_fast_transfer_status` (in `process_fin_transfer_to_near`) |

Neither set is checked by the other path's callback.

**`fast_fin_transfer` entry-point** (line 782) checks `is_unified_transfer_finalised` and then schedules a two-hop promise chain:

```
ft_transfer_call → ft_on_transfer → fast_fin_transfer
    └─ check_or_pay_ft_storage (block N+1)
           └─ fast_fin_transfer_to_near_callback (block N+2)
```

`add_fast_transfer` — which writes to `fast_transfers` — is called only inside `fast_fin_transfer_to_near_callback` at line 859, not in the entry-point. [1](#0-0) [2](#0-1) 

**`fast_fin_transfer_to_near_callback`** (lines 842–897) contains no check against `finalised_transfers` or `is_unified_transfer_finalised`. Its only duplicate-prevention is `add_fast_transfer`, which panics only if the same key already exists in `fast_transfers`. [3](#0-2) 

**`process_fin_transfer_to_near`** (lines 1873–1983) reads `fast_transfer_status` from `fast_transfers` (line 1884). If the entry is absent it treats the transfer as a plain (non-fast) transfer and mints/sends tokens to the original recipient. [4](#0-3) 

**Exploitable interleaving (NEAR receipt scheduling):**

```
Block N   : fast_fin_transfer runs; schedules check_or_pay_ft_storage receipt
Block N+1 : check_or_pay_ft_storage executes; schedules fast_fin_transfer_to_near_callback receipt
            fin_transfer submitted; schedules verify_proof receipt
Block N+2 : verify_proof executes; schedules fin_transfer_callback receipt
Block N+3 : fin_transfer_callback executes FIRST (receipt ordering)
              → add_fin_transfer(X) → finalised_transfers ← X inserted
              → get_fast_transfer_status(X) → None  (fast_transfers still empty)
              → send_tokens → mint(recipient, amount)   ← CREDIT #1
            fast_fin_transfer_to_near_callback executes SECOND
              → add_fast_transfer(X) → fast_transfers ← X inserted (no check of finalised_transfers)
              → send_tokens → mint(recipient, amount)   ← CREDIT #2
              → resolve_fast_transfer → burn_tokens_if_needed (burns relayer's deposited tokens)
```

For **bridged (deployed) tokens**, `send_tokens` calls `mint` on the token contract (line 2102), which creates new tokens unconditionally — the bridge does not need a pre-existing balance. Both mints succeed. [5](#0-4) 

For **native tokens**, `send_tokens` calls `ft_transfer` from the bridge's own balance (line 2110–2111). The first call drains the bridge's balance; the second call fails at the token level, so native tokens are not double-credited. [6](#0-5) 

`add_fast_transfer` only guards against a duplicate key in `fast_transfers`; it never consults `finalised_transfers`. [7](#0-6) 

`is_unified_transfer_finalised` correctly maps a `UnifiedTransferId` to the `finalised_transfers` set, but it is never called from `fast_fin_transfer_to_near_callback`. [8](#0-7) 

---

### Impact Explanation

For every exploited bridged-token transfer of amount X:

- Recipient receives 2X tokens (two separate `mint` calls).
- The relayer's X deposited tokens are burned by `resolve_fast_transfer` (line 908).
- Net: X unbacked tokens permanently enter circulation, breaking the 1:1 backing invariant.

This is a **High / Critical** impact: unauthorized creation of wrapped bridge assets through a settlement race, producing unbacked supply. [9](#0-8) 

---

### Likelihood Explanation

- **Attacker role:** Must be a trusted relayer. Relayer status is permissionless — any account can call `apply_for_trusted_relayer` with a NEAR stake and become active after a waiting period. No privileged key is required. [10](#0-9) 

- **Timing:** The attacker submits `ft_transfer_call` (fast transfer) and `fin_transfer` (proof) in the same block or consecutive blocks. NEAR receipt ordering within a block is deterministic and observable; an attacker can craft the submission order to ensure `fin_transfer_callback` is processed before `fast_fin_transfer_to_near_callback`.

- **Cost vs. profit:** Attacker loses X tokens as relayer (burned), gains 2X tokens as recipient (controls recipient account), net +X tokens per exploit. Repeatable for every registered bridged token.

---

### Recommendation

At the top of `fast_fin_transfer_to_near_callback`, before calling `add_fast_transfer`, check whether the transfer has already been finalised and abort (refunding the relayer's tokens) if so:

```rust
#[private]
pub fn fast_fin_transfer_to_near_callback(
    &mut self,
    fast_transfer: &FastTransfer,
    storage_payer: AccountId,
    relayer_id: AccountId,
) -> PromiseOrValue<U128> {
    require!(
        Self::check_storage_balance_result(0),
        BridgeError::StorageRecipientOmitted.as_ref()
    );

    // NEW: abort if fin_transfer already finalised this transfer
    if self.is_unified_transfer_finalised(&fast_transfer.transfer_id) {
        // Refund the relayer's deposited tokens
        return PromiseOrValue::Value(fast_transfer.amount); // ft_on_transfer refund path
    }
    // ... rest of existing logic
}
```

Additionally, `add_fast_transfer` should assert `!is_unified_transfer_finalised` as a defence-in-depth guard.

---

### Proof of Concept

Stateful simulation (NEAR sandbox):

1. Deploy bridge + bridged token. Register a trusted relayer.
2. Relayer calls `ft_transfer_call(bridge, amount, FastFinTransfer{transfer_id: X, recipient: alice, ...})` — this schedules `fast_fin_transfer_to_near_callback` but does not execute it yet.
3. In the next block, submit `fin_transfer` with a valid proof for transfer X. Ensure `fin_transfer_callback` receipt is ordered before `fast_fin_transfer_to_near_callback` receipt in the same execution block.
4. Assert: `alice.balance == 2 * amount_without_fee` (double-credit).
5. Assert: `total_supply(bridged_token) == initial_supply + 2 * amount_without_fee - amount_without_fee` (net +X unbacked tokens).

The ordering can be forced in sandbox by submitting `fin_transfer` one block after `fast_fin_transfer` so both callbacks land in the same block with `fin_transfer_callback` queued first (it has one fewer hop in its receipt chain at that point).

### Citations

**File:** near/omni-bridge/src/lib.rs (L249-253)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** near/omni-bridge/src/lib.rs (L782-784)
```rust
        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }
```

**File:** near/omni-bridge/src/lib.rs (L842-860)
```rust
    #[private]
    pub fn fast_fin_transfer_to_near_callback(
        &mut self,
        #[serializer(borsh)] fast_transfer: &FastTransfer,
        #[serializer(borsh)] storage_payer: AccountId,
        #[serializer(borsh)] relayer_id: AccountId,
    ) -> Promise {
        require!(
            Self::check_storage_balance_result(0),
            BridgeError::StorageRecipientOmitted.as_ref()
        );

        let OmniAddress::Near(recipient) = fast_transfer.recipient.clone() else {
            env::panic_str(BridgeError::InvalidState.to_string().as_str())
        };

        let required_balance = self
            .add_fast_transfer(fast_transfer, relayer_id, storage_payer.clone())
            .saturating_add(ONE_YOCTO);
```

**File:** near/omni-bridge/src/lib.rs (L906-916)
```rust
    ) -> U128 {
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fast_transfer(fast_transfer_id);
            amount
        } else {
            U128(0)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1480-1488)
```rust
    pub fn is_unified_transfer_finalised(&self, transfer_id: &UnifiedTransferId) -> bool {
        match transfer_id.kind {
            TransferIdKind::Nonce(nonce) => self.finalised_transfers.contains(&TransferId {
                origin_chain: transfer_id.origin_chain,
                origin_nonce: nonce,
            }),
            TransferIdKind::Utxo(_) => self.finalised_utxo_transfers.contains(transfer_id),
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1880-1907)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];

        // If fast transfer happened, change recipient and fee recipient to the relayer that executed fast transfer
        let (recipient, msg, fee_recipient) = match fast_transfer_status {
            Some(status) => {
                require!(
                    !status.finalised,
                    BridgeError::FastTransferAlreadyFinalised.as_ref()
                );
                self.remove_fast_transfer(&fast_transfer.id());
                (status.relayer.clone(), String::new(), status.relayer)
            }
            None => (
                recipient,
                transfer_message.msg.clone(),
                predecessor_account_id.clone(),
            ),
        };
```

**File:** near/omni-bridge/src/lib.rs (L2087-2106)
```rust
        } else if is_deployed_token {
            let deposit = if msg.is_empty() {
                NO_DEPOSIT
            } else {
                ONE_YOCTO
            };

            require!(
                ft_transfer_call_gas >= MIN_FT_TRANSFER_CALL_GAS,
                BridgeError::NotEnoughGasForTokenTransfer(ft_transfer_call_gas).as_ref()
            );

            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
                )
```

**File:** near/omni-bridge/src/lib.rs (L2107-2111)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
```

**File:** near/omni-bridge/src/lib.rs (L2251-2272)
```rust
    fn add_fast_transfer(
        &mut self,
        fast_transfer: &FastTransfer,
        relayer: AccountId,
        storage_owner: AccountId,
    ) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.fast_transfers
                .insert(
                    &fast_transfer.id(),
                    &FastTransferStatusStorage::V0(FastTransferStatus {
                        relayer,
                        storage_owner,
                        finalised: false,
                    }),
                )
                .is_none(),
            BridgeError::FastTransferAlreadyPerformed.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
```
