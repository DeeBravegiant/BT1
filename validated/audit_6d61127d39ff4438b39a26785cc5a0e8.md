### Title
Immediately Mutable Global Contract Under `AccountId` Mode Allows Deployer to Execute Arbitrary Code on All Subscriber Accounts Without Timelock - (`runtime/runtime/src/global_contracts.rs`)

### Summary
`GlobalContractDeployMode::AccountId` allows any NEAR account to deploy a global contract that is referenced by other accounts using `GlobalContractIdentifier::AccountId(deployer_id)`. The deployer can immediately replace the contract code at any time with no timelock, delay, or warning mechanism. All accounts that have opted into the contract via `UseGlobalContractAction` will silently execute the new (potentially malicious) code on their next function call, enabling the deployer to drain balances, corrupt storage, or perform any action the victim accounts are authorized to do.

### Finding Description
`GlobalContractDeployMode::AccountId` is explicitly documented as allowing the owner to update the contract for all its users: [1](#0-0) 

When a user calls `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(deployer_id)`, the account's contract field is set to `AccountContract::GlobalByAccount(deployer_id)`: [2](#0-1) 

The deployer can immediately push a new contract via `action_deploy_global_contract`, which calls `initiate_distribution` with no delay or timelock check: [3](#0-2) 

`initiate_distribution` immediately writes a new nonce and queues a `GlobalContractDistributionReceipt` that propagates the new code to all shards: [4](#0-3) 

The only validation performed on `DeployGlobalContractAction` is a size check — there is no timelock, no delay, and no mechanism for subscriber accounts to react before the new code takes effect: [5](#0-4) 

### Impact Explanation
Once the malicious contract is distributed (which happens within a few blocks across all shards), every subsequent function call on any subscriber account executes the new code. A malicious deployer contract can:
- Issue `promise_batch_action_transfer` to drain the subscriber account's NEAR balance
- Write or delete arbitrary keys in the subscriber account's storage
- Issue cross-contract calls on behalf of the subscriber account

The exact corrupted protocol values are: the NEAR balance (`Account::amount`) of all subscriber accounts, their storage trie entries under `TrieKey::ContractData`, and the global contract trie entry `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(deployer_id) }`.

### Likelihood Explanation
The `GlobalContractDeployMode::AccountId` mode is a production feature with documented upgrade semantics. Any account that deploys a popular shared contract (e.g., a wallet library, a token standard implementation, or a shared utility) and attracts many `UseGlobalContract` subscribers can exploit this. The attack requires only a standard signed transaction from the deployer — no validator or node-admin privileges. Users who opt into `GlobalContractIdentifier::AccountId` have no on-chain mechanism to detect or react to an upgrade before it takes effect.

### Recommendation
Implement a mandatory timelock for `GlobalContractDeployMode::AccountId` re-deployments. Specifically:
1. Store the block height of the last deployment under `TrieKey::GlobalContractNonce` or a new `TrieKey::GlobalContractUpdateTime` key.
2. In `action_deploy_global_contract`, reject re-deployments (nonce > 0) that occur within a configurable minimum delay (e.g., 7 days of blocks).
3. Alternatively, emit an on-chain event or pending-upgrade state that subscriber accounts can query, giving them time to switch to `GlobalContractIdentifier::CodeHash` (the immutable mode) before the new code activates.

### Proof of Concept
1. Deployer account `alice.near` submits `DeployGlobalContractAction { code: <legitimate_wasm>, deploy_mode: AccountId }` via RPC.
2. Many accounts submit `UseGlobalContractAction { contract_identifier: AccountId("alice.near") }`, setting their `AccountContract` to `GlobalByAccount("alice.near")`.
3. `alice.near` submits a second `DeployGlobalContractAction { code: <malicious_wasm>, deploy_mode: AccountId }`. `action_deploy_global_contract` immediately calls `initiate_distribution`, incrementing the nonce and queuing a `GlobalContractDistributionReceipt`.
4. Within a few blocks, `apply_distribution_current_shard` overwrites `TrieKey::GlobalContractCode { identifier: AccountId("alice.near") }` with the malicious bytecode on every shard.
5. Any subsequent function call on a subscriber account executes the malicious code, which calls `promise_batch_action_transfer` to drain the account's balance to `alice.near`. [6](#0-5)

### Citations

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

**File:** runtime/runtime/src/global_contracts.rs (L23-61)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}
```

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

**File:** runtime/runtime/src/global_contracts.rs (L141-168)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
```

**File:** runtime/runtime/src/global_contracts.rs (L189-232)
```rust
fn apply_distribution_current_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
) -> Result<Compute, RuntimeError> {
    let identifier = match &global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => GlobalContractCodeIdentifier::CodeHash(*hash),
        GlobalContractIdentifier::AccountId(account_id) => {
            GlobalContractCodeIdentifier::AccountId(account_id.clone())
        }
    };

    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    precompile_contract_with_warming(
        &ContractCode::new(global_contract_data.code().to_vec(), code_hash),
        config,
        apply_state.next_wasm_config.clone(),
        apply_state.cache.as_deref(),
    );
    near_vm_runner::report_metrics(apply_state.shard_id, "global_contract");
    let fees = &apply_state.config.fees;
    let per_byte_total = fees
        .deploy_global_contract_execution_per_byte
        .checked_mul(code_len)
        .ok_or(IntegerOverflowError)?;
    let compute = fees
        .deploy_global_contract_execution_base
        .checked_add(per_byte_total)
        .ok_or(IntegerOverflowError)?;
    Ok(compute)
```

**File:** runtime/runtime/src/action_validation.rs (L225-238)
```rust
/// Validates `DeployGlobalContractAction`. Checks that the given contract size doesn't exceed the limit.
fn validate_deploy_global_contract_action(
    limit_config: &LimitConfig,
    action: &DeployGlobalContractAction,
) -> Result<(), ActionsValidationError> {
    if action.code.len() as u64 > limit_config.max_contract_size {
        return Err(ActionsValidationError::ContractSizeExceeded {
            size: action.code.len() as u64,
            limit: limit_config.max_contract_size,
        });
    }

    Ok(())
}
```
