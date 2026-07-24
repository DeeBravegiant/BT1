### Title
Proof Replay via `remove_fin_transfer` Tombstone Erasure on `ft_transfer_call` Refund Path — (`near/omni-bridge/src/lib.rs`)

### Summary

When a `fin_transfer` to a NEAR recipient uses `ft_transfer_call` (non-empty `msg`) and the recipient's `ft_on_transfer` returns `U128(0)` (full refund), `fin_transfer_send_tokens_callback` calls `remove_fin_transfer`, which **permanently erases** the `TransferId` from `finalised_transfers`. A trusted relayer can then re-submit the identical proof, pass `add_fin_transfer`'s uniqueness check, and mint or unlock the same tokens a second time.

### Finding Description

**Root cause — `remove_fin_transfer` is called on the refund path:** [1](#0-0) 

When `is_refund_required` returns `true`, the callback:
1. Burns/re-locks the tokens (correctly reverting the first settlement)
2. Calls `remove_fin_transfer`, which **removes** the `TransferId` from `finalised_transfers` [2](#0-1) 

**`is_refund_required` trigger condition:** [3](#0-2) 

Returns `true` when `is_ft_transfer_call == true` (i.e., `msg` is non-empty) and the promise result is `Ok(U128(0))`. The `is_ft_transfer_call` flag is set at: [4](#0-3) 

**`add_fin_transfer` uniqueness check — only blocks if the ID is present:** [5](#0-4) 

After `remove_fin_transfer` erases the entry, `add_fin_transfer` on a second submission succeeds unconditionally.

**`fin_transfer` entry point — requires `#[trusted_relayer]`:** [6](#0-5) 

The `#[trusted_relayer]` macro is configured with `bypass_roles(Role::DAO, Role::UnrestrictedRelayer)`: [7](#0-6) 

Becoming a trusted relayer is a **publicly accessible, permissionless staking process** (`apply_for_trusted_relayer` + stake + waiting period). It is not a privileged operator role granted by an admin.

### Impact Explanation

After the tombstone is erased, a second `fin_transfer` with the identical proof:
- Passes `add_fin_transfer` (set no longer contains the ID)
- Mints or unlocks the same token amount a second time
- Credits the recipient twice for a single origin-chain event

This produces unbacked supply (for deployed/wrapped tokens) or drains the locked-token reserve (for native tokens), directly violating the one-settlement-per-origin-event invariant.

### Likelihood Explanation

The attacker must:
1. Become a trusted relayer (publicly accessible: stake NEAR, wait the waiting period)
2. Initiate a transfer on the origin chain to a recipient contract they control, with a non-empty `msg`
3. Ensure their recipient contract's `ft_on_transfer` returns `U128(0)` on the first call, then accepts on the second
4. Re-submit the identical `FinTransferArgs` proof

Steps 2–4 are fully attacker-controlled. Step 1 is permissionless. The staking requirement provides economic friction but not a security barrier — the double-minted tokens can far exceed the stake, and the stake is recoverable via `resign_trusted_relayer` after the attack.

### Recommendation

`finalised_transfers` must be a **permanent tombstone**. Remove the `remove_fin_transfer` call from the refund path entirely. Instead, on `ft_transfer_call` refund:

- Keep the `TransferId` in `finalised_transfers` permanently
- Burn/re-lock the tokens as currently done
- Emit `FailedFinTransferEvent` as currently done
- Do **not** erase the tombstone; accept the small storage cost as the price of replay protection

If storage reclamation is desired, use a separate "failed finalisations" set that is checked alongside `finalised_transfers` in `add_fin_transfer`, so the uniqueness invariant is never broken.

### Proof of Concept

Call sequence:

1. Attacker stakes required NEAR and waits the waiting period → becomes trusted relayer
2. Attacker sends tokens on origin chain to bridge, specifying recipient = attacker-controlled NEAR contract, `msg` = non-empty string
3. Attacker calls `fin_transfer` with valid proof → `add_fin_transfer` inserts `TransferId`, tokens minted, `ft_transfer_call` dispatched to recipient
4. Attacker's recipient contract `ft_on_transfer` returns `U128(0)` → full refund
5. `fin_transfer_send_tokens_callback` fires: `burn_tokens_if_needed` (async, detached), `revert_lock_actions`, **`remove_fin_transfer`** (tombstone erased), `FailedFinTransferEvent` emitted
6. Attacker re-submits identical `FinTransferArgs` to `fin_transfer` → `add_fin_transfer` succeeds (set is empty for this ID), tokens minted/unlocked again, `ft_transfer_call` dispatched
7. Attacker's recipient contract `ft_on_transfer` returns `U128(amount)` → tokens accepted
8. Recipient balance = 2× the bridged amount; origin chain event settled twice

A stateful integration test can confirm this by checking `is_transfer_finalised` returns `false` after step 5 and that the recipient balance doubles after step 7.

### Citations

**File:** near/omni-bridge/src/lib.rs (L249-253)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** near/omni-bridge/src/lib.rs (L674-700)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
        require!(
            args.storage_deposit_actions.len() <= 3,
            BridgeError::InvalidStorageAccountsLen.as_ref()
        );
        let mut main_promise = self.verify_proof(args.chain_kind, args.prover_args);

        let mut attached_deposit = env::attached_deposit();

        for action in &args.storage_deposit_actions {
            main_promise =
                main_promise.and(Self::check_or_pay_ft_storage(action, &mut attached_deposit));
        }

        main_promise.then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(attached_deposit)
                .with_static_gas(FIN_TRANSFER_CALLBACK_GAS)
                .fin_transfer_callback(
                    &args.storage_deposit_actions,
                    env::predecessor_account_id(),
                ),
        )
    }
```

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

**File:** near/omni-bridge/src/lib.rs (L1789-1809)
```rust
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1972-1982)
```rust
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
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
