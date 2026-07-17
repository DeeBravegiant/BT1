### Title
Global Contract `AccountId` Deploy Mode Allows Deployer to Silently Replace Contract Code for All Subscriber Accounts - (`File: runtime/runtime/src/global_contracts.rs`)

### Summary
The `GlobalContractDeployMode::AccountId` feature allows any NEAR account to deploy a global contract whose code is stored under the deployer's account ID as a mutable key. Any account that opts in via `UseGlobalContract` with `GlobalContractIdentifier::AccountId(deployer_id)` permanently delegates its executable code identity to the deployer. The deployer can replace the contract code at any time with a single signed transaction, and every subsequent function call on every subscriber account will silently execute the new (potentially malicious) code. This is the direct nearcore analog of the `setNewAddresses` front-running class: a mutable privileged address that can redirect user execution to attacker-controlled logic.

### Finding Description

**Step 1 – Deployer registers a global contract under their account ID.**

`action_deploy_global_contract` in `global_contracts.rs` calls `initiate_distribution`, which builds the `GlobalContractIdentifier` from the deployer's `account_id` when `deploy_mode == AccountId`:

```rust
GlobalContractDeployMode::AccountId => {
    GlobalContractIdentifier::AccountId(account_id.clone())
}
```

The code is stored at `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(deployer_id) }`. There is no version lock, no time-lock, and no consent mechanism for subscribers. [1](#0-0) 

**Step 2 – Subscriber account opts in.**

`use_global_contract` sets the subscriber's `AccountContract` to `AccountContract::GlobalByAccount(deployer_id)`. From this point on the subscriber's executable identity is a live pointer into the deployer's mutable slot:

```rust
AccountContract::GlobalByAccount(id.clone())
``` [2](#0-1) 

**Step 3 – Every function call resolves the contract at execution time.**

`RuntimeContractIdentifier::resolve` in `contract_code.rs` converts `AccountContract::GlobalByAccount(id)` into a `GlobalContractIdentifier::AccountId(id)` and immediately looks up the *current* code hash from the trie. There is no snapshot of the code hash at `UseGlobalContract` time:

```rust
Ok(gci) => {
    let code_hash = gci.clone().hash(state_update, access)?;
    return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
}
``` [3](#0-2) 

**Step 4 – Deployer replaces the contract.**

The deployer submits a new `DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId`. `check_actor_permissions` only requires `actor_id == account_id`, which the deployer satisfies. `increment_nonce` ensures the new distribution receipt wins the freshness check and overwrites the stored code on every shard: [4](#0-3) [5](#0-4) 

**Step 5 – Malicious code executes for all subscribers.**

The next function call on any subscriber account resolves to the new malicious code hash. The malicious Wasm can call `promise_batch_action_transfer` to drain the subscriber's balance, corrupt storage, or emit fraudulent receipts. [6](#0-5) 

### Impact Explanation

**Impact: High.**

Every account that has called `UseGlobalContract` with `GlobalContractIdentifier::AccountId(deployer_id)` is affected simultaneously. The malicious contract can:
- Transfer the subscriber account's entire NEAR balance to the attacker (corrupted `balance` DB entry).
- Overwrite the subscriber's contract storage (corrupted trie state).
- Return fraudulent values from callbacks, corrupting downstream receipt outcomes.

The corrupted protocol values are: subscriber account balances, account storage state, and function-call receipt results. [7](#0-6) 

### Likelihood Explanation

**Likelihood: Low.**

The attack requires a malicious or compromised deployer account. Subscribers must have explicitly called `UseGlobalContract` with the deployer's account ID, accepting the mutable relationship. However, the protocol provides no warning that the contract is mutable, no version-pinning mechanism, and no time-lock. A deployer who presents a legitimate contract initially and later turns malicious (or whose key is compromised) can exploit all subscribers without any additional on-chain permission.

### Recommendation

1. **Introduce a version-pinning option:** Allow `UseGlobalContract` to optionally record the code hash at subscription time (`AccountContract::GlobalByAccountAtHash(id, hash)`). Function calls would then verify the current code hash matches before executing.
2. **Add an update time-lock:** Require a mandatory delay (e.g., one epoch) between a `DeployGlobalContract` update and it taking effect for existing subscribers, giving them time to migrate.
3. **Emit an on-chain event / require re-consent:** Force subscribers to re-issue `UseGlobalContract` after each deployer update, so the change is never silent.

### Proof of Concept

```
1. Attacker (alice.near) deploys a legitimate global contract:
   DeployGlobalContractAction { code: <legit_wasm>, deploy_mode: AccountId }
   → stored at TrieKey::GlobalContractCode { AccountId("alice.near") }

2. Victim (bob.near) opts in:
   UseGlobalContractAction { contract_identifier: AccountId("alice.near") }
   → bob.near.contract = AccountContract::GlobalByAccount("alice.near")

3. Attacker replaces the contract with malicious code:
   DeployGlobalContractAction { code: <malicious_wasm>, deploy_mode: AccountId }
   → TrieKey::GlobalContractCode { AccountId("alice.near") } overwritten

4. Any caller invokes a function on bob.near:
   FunctionCallAction { method_name: "withdraw", ... }
   → RuntimeContractIdentifier::resolve reads current code hash for "alice.near"
   → malicious_wasm executes, calls promise_batch_action_transfer draining bob.near's balance
   → bob.near balance = 0, attacker balance += stolen amount
```

The entire attack is executed through standard signed transactions submitted via public RPC. No validator, block producer, or node-admin privileges are required.

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

**File:** runtime/runtime/src/global_contracts.rs (L202-210)
```rust
    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
```

**File:** runtime/runtime/src/contract_code.rs (L43-46)
```rust
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
```

**File:** runtime/runtime/src/actions.rs (L718-731)
```rust
        Action::DeployContract(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::WithdrawFromGasKey(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
```

**File:** runtime/runtime/src/lib.rs (L628-662)
```rust
            Action::FunctionCall(function_call) => {
                metrics::ACTION_CALLED_COUNT.function_call.inc();
                let account = account.as_mut().expect(EXPECT_ACCOUNT_EXISTS);
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
                let contract = preparation_pipeline.get_contract(
                    receipt,
                    contract_id.clone(),
                    action_index,
                    None,
                );
                let is_last_action = action_index + 1 == actions.len();
                action_function_call(
                    state_update,
                    apply_state,
                    account,
                    receipt,
                    action_receipt,
                    promise_results,
                    &mut result,
                    account_id,
                    function_call,
                    action_hash,
                    &contract_id,
                    &apply_state.config,
                    is_last_action,
                    epoch_info_provider,
                    contract,
                )?;
```

**File:** core/primitives/src/action/mod.rs (L133-141)
```rust
pub enum GlobalContractDeployMode {
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
```
