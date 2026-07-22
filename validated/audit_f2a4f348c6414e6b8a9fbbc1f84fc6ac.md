### Title
Invoke Signature Verification Bypassed at Gateway Admission via Weak `account_tx_in_pool_or_recent_block` Proxy Check — (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The `skip_stateful_validations` function in the gateway's stateful validator skips the `__validate__` entry-point (account signature verification) for invoke transactions with nonce 1 when `account_tx_in_pool_or_recent_block` returns `true`. The check is documented as a proxy for "a deploy_account transaction exists for this account," but it actually returns `true` for **any** transaction in the mempool for that address — including a deploy_account submitted by the attacker themselves using a dummy-validator class. An attacker can therefore get an invoke transaction with an arbitrary (invalid) signature admitted to the mempool without signature verification.

---

### Finding Description

`skip_stateful_validations` in `crates/apollo_gateway/src/stateful_transaction_validator.rs` (lines 429–461) fires when three conditions hold simultaneously:

1. The transaction is an `Invoke` with `tx.nonce() == Nonce(Felt::ONE)`
2. The on-chain account nonce is `Nonce(Felt::ZERO)` (account not yet deployed)
3. `mempool_client.account_tx_in_pool_or_recent_block(sender_address)` returns `true` [1](#0-0) 

When all three hold, `run_validate_entry_point` is called with `skip_validate = true`, which sets `ExecutionFlags { validate: false, … }` and skips the account's `__validate__` entry point entirely: [2](#0-1) 

The code comment asserts: *"It is sufficient to check if the account exists in the mempool since it means that either it has a deploy_account transaction or transactions with future nonces that passed validations."* [3](#0-2) 

However, `account_tx_in_pool_or_recent_block` is implemented as:

```rust
pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
    self.state.contains_account(account_address)
        || self.tx_pool.contains_account(account_address)
}
``` [4](#0-3) 

`tx_pool.contains_account` returns `true` for **any** transaction in the pool for that address — it does not distinguish between `DeployAccount` and `Invoke` transaction types: [5](#0-4) 

An attacker can therefore:

1. Compute a target address `A` using a class hash with a dummy `__validate_deploy__` (always returns `VALIDATED`).
2. Submit a `DeployAccount` for `A` using that class. This passes the gateway's stateful validation because `__validate_deploy__` succeeds.
3. Now `tx_pool.contains_account(A)` is `true`.
4. Submit an `Invoke` with `nonce = 1` for `A` carrying an **arbitrary or empty signature**.
5. `skip_stateful_validations` fires: `account_nonce == 0`, `tx_nonce == 1`, `account_tx_in_pool_or_recent_block == true` → `skip_validate = true`.
6. `run_validate_entry_point` is called with `validate: false`; the account's `__validate__` is never executed.
7. The invalid invoke is forwarded to the mempool and admitted. [6](#0-5) 

The gateway then calls `mempool_client.add_tx(add_tx_args)` with the unauthenticated transaction: [7](#0-6) 

---

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

The gateway's admission invariant is that every invoke transaction reaching the mempool has passed account signature verification (`__validate__`). This invariant is broken: an invoke with a completely invalid (or absent) signature is admitted to the mempool. The batcher will later pick it up, attempt execution, and the blockifier will call `__validate__` at execution time (since `new_for_sequencing` sets `validate: true`), causing the transaction to revert. However, the admission-level invariant is violated: the mempool contains a transaction that was never authenticated.

Consequences:
- Mempool capacity is consumed by unauthenticated transactions.
- The batcher wastes execution resources on transactions that will always revert.
- An attacker can repeatedly inject invalid invokes for any account that has a pending `DeployAccount`, degrading throughput and delaying legitimate transactions.
- The attacker can also front-run a legitimate user's `deploy_account + invoke(nonce=1)` pair by injecting a competing invalid invoke with a higher tip, causing the batcher to execute the invalid one first (which reverts), before the legitimate one.

---

### Likelihood Explanation

**Medium.** The attacker needs to:
- Know or compute a target account address (trivial given deterministic address derivation).
- Have access to a declared class with a permissive `__validate_deploy__` (such classes exist on Starknet mainnet/testnet).
- Submit two transactions (one `DeployAccount`, one `Invoke`) through the public RPC endpoint.

No privileged access is required. The attack is fully unprivileged and repeatable.

---

### Recommendation

Replace the weak `account_tx_in_pool_or_recent_block` proxy with a check that specifically verifies a `DeployAccount` transaction exists in the pool for the sender address. The mempool should expose a dedicated query such as `deploy_account_in_pool(address) -> bool` that inspects the transaction type, rather than returning `true` for any transaction type. Alternatively, the `skip_stateful_validations` path should be restricted to cases where the mempool can confirm the pending transaction is specifically a `DeployAccount`.

---

### Proof of Concept

```
1. Attacker selects a class hash C with a dummy __validate_deploy__ (always returns VALIDATED).
   C is already declared on the network.

2. Attacker computes address A = hash(C, salt, constructor_calldata, deployer=0).

3. Attacker submits DeployAccount(class_hash=C, salt=..., nonce=0) for address A.
   → Gateway: stateless validation passes.
   → Gateway: stateful validation calls __validate_deploy__ on C → VALIDATED.
   → Mempool: tx_pool now contains a DeployAccount for address A.
   → account_tx_in_pool_or_recent_block(A) == true.

4. Attacker submits Invoke(sender=A, nonce=1, signature=[0xdead, 0xbeef]).
   → Gateway: stateless validation passes (no signature format check).
   → Gateway: get_nonce_from_state(A) == 0 (A not deployed yet).
   → skip_stateful_validations: nonce==1, account_nonce==0,
     account_tx_in_pool_or_recent_block(A)==true → returns true (skip).
   → run_validate_entry_point called with validate=false → __validate__ NOT called.
   → Mempool: invalid invoke admitted with signature [0xdead, 0xbeef].

5. Batcher picks up both transactions.
   → DeployAccount executes: A is deployed with class C.
   → Invoke executes: blockifier calls __validate__ → signature check fails → REVERTED.

Result: An invoke with an invalid signature was admitted to the mempool and
        consumed batcher resources, violating the gateway admission invariant.
``` [8](#0-7) [4](#0-3) [9](#0-8)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L175-178)
```rust
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-315)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };

        let account_tx = AccountTransaction { tx: executable_tx.clone(), execution_flags };

```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L429-461)
```rust
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
    }

    Ok(false)
}
```

**File:** crates/apollo_mempool/src/mempool.rs (L115-117)
```rust
    fn contains_account(&self, address: ContractAddress) -> bool {
        self.staged.contains_key(&address) || self.committed.contains_key(&address)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L697-700)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L201-203)
```rust
    pub fn contains_account(&self, address: ContractAddress) -> bool {
        self.txs_by_account.contains(address)
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L275-286)
```rust
        let add_tx_args = AddTransactionArgsWrapper {
            args: AddTransactionArgs::new(internal_tx, nonce),
            p2p_message_metadata,
        };

        // Await as late as possible for proof archiving before sending the transaction to the
        // mempool.
        Self::await_proof_archiving(proof_archive_handle)
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let mempool_client_result = self.mempool_client.add_tx(add_tx_args).await;
```
