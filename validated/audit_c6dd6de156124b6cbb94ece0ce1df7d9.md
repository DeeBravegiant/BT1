### Title
Mutable Global Contract (`AccountId` Mode) Gives Deployer Persistent Code-Execution Control Over All Adopting Accounts - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
`GlobalContractDeployMode::AccountId` is the nearcore analog of the Marmo `delegatecall`-to-caller-chosen-implementation vulnerability. Any account that calls `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(deployer)` permanently delegates code-execution authority over its own account context to the deployer. The deployer can silently replace the WASM at any time; the next function call on the victim account executes the new code with full access to the victim's balance, storage, and promise-creation capability.

### Finding Description

**Vulnerability class**: Caller-controlled code execution in a privileged context (runtime state mismatch / balance/storage corruption).

**Root cause — `GlobalContractDeployMode::AccountId`**

When a deployer submits `DeployGlobalContractAction { deploy_mode: AccountId }`, `initiate_distribution` in `global_contracts.rs` stores the contract under the key `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(deployer_account_id) }`: [1](#0-0) 

The deployer can call `DeployGlobalContractAction` again at any time with new WASM. The nonce mechanism only prevents stale distribution receipts from overwriting newer ones — it does not prevent the deployer from issuing a new, higher-nonce deployment: [2](#0-1) 

**Root cause — `UseGlobalContractAction` binds the account permanently**

When a victim account calls `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(deployer)`, `use_global_contract` sets the account's contract field to `AccountContract::GlobalByAccount(deployer_id)`: [3](#0-2) 

**Root cause — function call loads live code from the deployer's key**

On every subsequent `FunctionCall` to the victim account, the runtime resolves the contract via `RuntimeContractIdentifier::resolve`, which reads the current bytes stored under `GlobalContractCodeIdentifier::AccountId(deployer)` at call time: [4](#0-3) 

There is no snapshot, no version pin, and no consent mechanism. Whatever WASM the deployer has most recently distributed is what executes in the victim's account context.

**The design is explicit**

The `AccountId` variant is documented as intentionally mutable: [5](#0-4) 

This mirrors the Marmo situation exactly: the system is designed this way, but the trust implication — that the deployer retains permanent, unilateral code-execution authority over every adopting account — is not enforced or surfaced at the protocol level.

### Impact Explanation

Malicious WASM executing in the victim account's context can:

- **Drain balance**: issue `promise_batch_action_transfer` to transfer the victim's NEAR to an attacker-controlled account.
- **Corrupt storage**: write arbitrary key-value pairs into the victim's trie storage.
- **Hijack access keys**: issue `promise_batch_action_add_key_with_full_access` to add an attacker-controlled full-access key, or `promise_batch_action_delete_key` to remove the victim's keys.

All of these are reachable through the standard NEAR host functions exposed to WASM: [6](#0-5) 

The corrupted protocol values are: **account balance**, **account storage trie entries**, and **account access-key set** — all of which affect the state root of the shard containing the victim account.

### Likelihood Explanation

**Realistic attack path (unprivileged external user)**:

1. Attacker deploys a benign, useful global contract with `GlobalContractDeployMode::AccountId` via a public RPC transaction.
2. Victim accounts voluntarily call `UseGlobalContractAction { contract_identifier: AccountId(attacker) }` — a normal, signed user transaction.
3. Attacker later submits a new `DeployGlobalContractAction` with malicious WASM under the same `AccountId` key.
4. On the next `FunctionCall` to any victim account, the malicious WASM executes in that account's context.

No validator, node admin, or trusted-service privilege is required. The attacker needs only a funded NEAR account and the ability to submit transactions via public RPC. The `GlobalContractDeployMode::AccountId` feature is production-enabled and reachable through the standard transaction pipeline.

The `test_global_contract_update` test confirms that this update path works as intended: [7](#0-6) 

### Recommendation

1. **Warn at the protocol level**: When `UseGlobalContractAction` is processed with `AccountId` mode, emit a receipt-level log or structured event making the trust delegation explicit and auditable.
2. **Consider a consent mechanism**: Require the adopting account to re-confirm (e.g., via a versioned identifier or an explicit opt-in to updates) before a new deployer version takes effect on their account.
3. **Document the trust model prominently**: The `AccountId` mode should carry a clear, protocol-level warning that the deployer retains unilateral code-execution authority over all adopting accounts indefinitely.
4. **Prefer `CodeHash` mode** for any use case where immutability is desired; tooling and SDKs should default to `CodeHash` and require explicit opt-in for `AccountId`.

### Proof of Concept

```
1. attacker.near submits:
   DeployGlobalContractAction { code: <benign_wasm>, deploy_mode: AccountId }
   → stored at TrieKey::GlobalContractCode { identifier: AccountId("attacker.near") }

2. victim.near submits:
   UseGlobalContractAction { contract_identifier: AccountId("attacker.near") }
   → victim.near.contract = AccountContract::GlobalByAccount("attacker.near")

3. attacker.near submits:
   DeployGlobalContractAction { code: <malicious_wasm>, deploy_mode: AccountId }
   → overwrites TrieKey::GlobalContractCode { identifier: AccountId("attacker.near") }
   (nonce incremented, distribution propagates to all shards)

4. anyone calls:
   FunctionCall { receiver_id: "victim.near", method_name: "any_method", ... }
   → runtime resolves AccountContract::GlobalByAccount("attacker.near")
   → loads <malicious_wasm> from trie
   → executes in victim.near's context
   → malicious_wasm calls promise_batch_action_transfer(victim_balance → attacker.near)
   → victim.near's balance is drained; state root of victim's shard is corrupted
```

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L93-105)
```rust
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract);
```

**File:** runtime/runtime/src/global_contracts.rs (L149-156)
```rust
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
```

**File:** runtime/runtime/src/global_contracts.rs (L238-255)
```rust
fn check_and_update_nonce(
    global_contract_data: &GlobalContractDistributionReceipt,
    identifier: &GlobalContractCodeIdentifier,
    state_update: &mut TrieUpdate,
) -> Result<bool, RuntimeError> {
    let nonce_key = TrieKey::GlobalContractNonce { identifier: identifier.clone() };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;
    let incoming_nonce = global_contract_data.nonce();

    // Allow the same nonce since the nonce is updated immediately when
    // initiating distribution to prevent multiple distributions with the same
    // nonce from being initiated.
    if incoming_nonce < stored_nonce {
        return Ok(false);
    }

    set_nonce(state_update, nonce_key, incoming_nonce);
    Ok(true)
```

**File:** runtime/runtime/src/lib.rs (L631-638)
```rust
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
```

**File:** core/primitives/src/action/mod.rs (L138-142)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
}
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2925-2944)
```rust
    ///
    /// * If `promise_idx` does not correspond to an existing promise returns `InvalidPromiseIndex`.
    /// * If the promise pointed by the `promise_idx` is an ephemeral promise created by
    /// `promise_and` returns `CannotAppendActionToJointPromise`.
    /// * If `method_name_len + method_name_ptr` or `arguments_len + arguments_ptr` or
    /// `amount_ptr + 16` points outside the memory of the guest or host returns
    /// `MemoryAccessViolation`.
    /// * If called as view function returns `ProhibitedInView`.
    pub fn promise_batch_action_function_call_weight(
        &mut self,
        promise_idx: u64,
        method_name_len: u64,
        method_name_ptr: u64,
        arguments_len: u64,
        arguments_ptr: u64,
        amount_ptr: u64,
        gas: u64,
        gas_weight: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L72-106)
```rust
fn test_global_contract_update() {
    let mut env = GlobalContractsTestEnv::setup(Balance::from_near(1000));
    let use_accounts = [env.account_shard_0.clone(), env.account_shard_1.clone()];

    env.deploy_trivial_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        env.use_global_contract(
            account,
            GlobalContractIdentifier::AccountId(env.deploy_account.clone()),
        );

        // Currently deployed trivial contract doesn't have any methods,
        // so we expect any function call to fail with MethodNotFound error
        let call_tx = env.call_global_contract_tx(account.clone(), account.clone());
        let call_outcome = env.execute_tx(call_tx);
        assert_matches!(
            call_outcome.status,
            FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
                kind: ActionErrorKind::FunctionCallError(FunctionCallError::MethodResolveError(
                    MethodResolveError::MethodNotFound
                )),
                index: _
            }))
        );
    }

    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        // Function call should be successful after deploying rs contract
        // containing the function we call here
        env.assert_call_global_contract_success(account.clone(), account.clone());
    }
}
```
