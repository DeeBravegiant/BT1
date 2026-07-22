The vulnerability claim is **valid**. Here is the full analysis:

---

### Title
Cairo0 Class Temporal Isolation Bypass in `estimate_fee` via Missing `is_declared` Check — (`crates/apollo_rpc_execution/src/state_reader.rs`)

### Summary
When `ExecutionStateReader::get_compiled_class` is called through the `class_manager_handle` path, Cairo1 (V1) classes are gated by an `is_contract_class_declared` check against `self.state_number`, but Cairo0 (V0/deprecated) classes are returned unconditionally. An unprivileged RPC caller can invoke `estimate_fee` at a historical block number with a transaction that references a Cairo0 class declared *after* that block, causing execution to use a class that should be invisible at the requested state.

### Finding Description

In `get_compiled_class`, when `class_manager_handle` is `Some`, the code takes the class manager path: [1](#0-0) 

For `ContractClass::V1`, `is_contract_class_declared` is called with `self.state_number` — if the class was declared after the requested block, `UndeclaredClassHash` is returned: [2](#0-1) 

For `ContractClass::V0`, **no such check exists**. The class is returned directly, with a developer TODO acknowledging the gap: [3](#0-2) 

The fallback path (no class manager) is safe because it calls `get_deprecated_class_definition_at(state_number, class_hash)`, which enforces the block-number boundary: [4](#0-3) 

The `is_contract_class_declared` helper itself correctly enforces the boundary for Cairo1: [5](#0-4) 

The `estimate_fee` RPC handler passes `class_manager_client` into the `ExecutionStateReader` whenever the server is configured with one (the production sequencer path): [6](#0-5) 

`JsonRpcServerImpl` holds an `Option<SharedClassManagerClient>` that is `Some` in production deployments: [7](#0-6) 

### Impact Explanation

An unprivileged caller can submit an `estimate_fee` request at block N referencing a Cairo0 class declared at block N+k. The execution engine loads and runs that class, producing a fee estimate that is computed against a class that does not exist at the requested state. This is a **wrong authoritative-looking value** returned by the RPC fee estimation endpoint, matching the High impact category: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

- Requires `class_manager_client` to be `Some` — this is the production sequencer configuration.
- The attacker only needs to know a Cairo0 class hash declared after the target block (publicly observable on-chain).
- A `deploy_account` or `invoke` transaction referencing that class hash is sufficient to trigger the path.
- No privileged access is required.

### Recommendation

Apply the same `is_contract_class_declared` guard to the `ContractClass::V0` arm. The storage layer already has `get_deprecated_class_definition_block_number` to support this check: [8](#0-7) 

The fix mirrors the existing Cairo1 guard: fetch the deprecated class's declaration block number and return `StateError::UndeclaredClassHash(class_hash)` if `state_number.is_before(declaration_block)`. The TODO comment at line 136 already tracks this work.

### Proof of Concept

1. Declare a Cairo0 class at block 1 (write it to storage via `append_state_diff` with `deprecated_declared_classes`).
2. Construct an `ExecutionStateReader` with `state_number = StateNumber::unchecked_right_after_block(BlockNumber(0))` and a mock `class_manager_client` that returns `ContractClass::V0(...)` for that class hash.
3. Call `exec_state_reader.get_compiled_class(cairo0_class_hash)`.
4. **Observed**: returns `Ok(RunnableCompiledClass::V0(...))`.
5. **Expected**: returns `Err(StateError::UndeclaredClassHash(...))`.

The existing test at `crates/apollo_rpc_execution/src/state_reader_test.rs` covers the Cairo1 case (class declared at block 1 is invisible at block 0) but has no equivalent test for Cairo0 via the class manager path: [9](#0-8)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L121-141)
```rust
            return match contract_class {
                ContractClass::V1(casm_contract_class) => {
                    let is_declared = is_contract_class_declared(
                        &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
                        &class_hash,
                        self.state_number,
                    )
                    .map_err(|e| StateError::StateReadError(e.to_string()))?;

                    if is_declared {
                        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
                    } else {
                        Err(StateError::UndeclaredClassHash(class_hash))
                    }
                }
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
            };
```

**File:** crates/apollo_storage/src/state/mod.rs (L514-519)
```rust
    pub fn get_deprecated_class_definition_block_number(
        &self,
        class_hash: &ClassHash,
    ) -> StorageResult<Option<BlockNumber>> {
        Ok(self.deprecated_declared_classes_block_table.get(self.txn, class_hash)?)
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L531-546)
```rust
    pub fn get_deprecated_class_definition_at(
        &self,
        state_number: StateNumber,
        class_hash: &ClassHash,
    ) -> StorageResult<Option<DeprecatedContractClass>> {
        let Some(value) = self.deprecated_declared_classes_table.get(self.txn, class_hash)? else {
            return Ok(None);
        };
        if state_number.is_before(value.block_number) {
            return Ok(None);
        }
        // TODO(shahak): Fix code duplication with ClassStorageReader.
        Ok(Some(
            self.file_handlers.get_deprecated_contract_class_unchecked(value.location_in_file)?,
        ))
    }
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L53-62)
```rust
pub(crate) fn is_contract_class_declared(
    txn: &StorageTxn<'_, RO>,
    class_hash: &ClassHash,
    state_number: StateNumber,
) -> Result<bool, ExecutionUtilsError> {
    Ok(txn
        .get_state_reader()?
        .get_class_definition_block_number(class_hash)?
        .is_some_and(|block_number| state_number.is_after(block_number)))
}
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L160-172)
```rust
pub struct JsonRpcServerImpl {
    pub chain_id: ChainId,
    pub execution_config: ExecutionConfig,
    pub storage_reader: StorageReader,
    pub max_events_chunk_size: usize,
    pub max_events_keys: usize,
    pub starting_block: BlockHashAndNumber,
    pub shared_highest_block: Arc<RwLock<Option<BlockHashAndNumber>>>,
    pub pending_data: Arc<RwLock<PendingData>>,
    pub pending_classes: Arc<RwLock<PendingClasses>>,
    pub writer_client: Arc<dyn StarknetWriter>,
    pub class_manager_client: Option<SharedClassManagerClient>,
}
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1030-1044)
```rust
        let class_manager_client =
            create_class_manager_client(self.class_manager_client.clone()).await;

        let estimate_fee_result = tokio::task::spawn_blocking(move || {
            exec_estimate_fee(
                executable_txns,
                &chain_id,
                reader,
                maybe_pending_data,
                state_number,
                block_number,
                &execution_config,
                validate,
                DONT_IGNORE_L1_DA_MODE,
                class_manager_client,
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L370-372)
```rust
    // Class with hash 0x3 is not yet declared.
    let result = exec_state_reader.get_compiled_class(class_hash_0x3);
    assert_matches!(result, Err(StateError::UndeclaredClassHash(hash)) if hash == class_hash_0x3);
```
