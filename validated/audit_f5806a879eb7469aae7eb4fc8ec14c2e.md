### Title
`SyncStateReader::get_compiled_class_hash` Is Unimplemented (`todo!()`), Causing Gateway Stateful Validation to Panic for Declare Transactions — (`File: crates/apollo_gateway/src/sync_state_reader.rs`)

### Summary

The `BlockifierStateReader` implementation for `SyncStateReader` — the state reader used by the gateway for stateful transaction validation — leaves `get_compiled_class_hash` as `todo!()`. Any transaction validation path that causes the blockifier's `CachedState` to call `get_compiled_class_hash` on the underlying reader will trigger a Rust panic, crashing the validation task and causing the gateway to reject the transaction.

### Finding Description

`SyncStateReader` implements `BlockifierStateReader` for the gateway's stateful validation path. Every method in the trait is implemented except `get_compiled_class_hash`, which is left as `todo!()`: [1](#0-0) 

`SyncStateReader` is wrapped in `SyncOrGenesisStateReader`, which unconditionally delegates `get_compiled_class_hash` to the inner `SyncStateReader`: [2](#0-1) 

`SyncOrGenesisStateReader` is the concrete type returned by `SyncStateReaderFactory::get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block`, which is the factory used for all gateway stateful validation: [3](#0-2) 

The blockifier's `CachedState::get_compiled_class_hash` calls through to the underlying state reader whenever the compiled class hash for a given class is not yet in the local cache: [4](#0-3) 

For `DeclareV2`/`DeclareV3` transactions, the blockifier reads the compiled class hash of the declared class (to verify it is not already declared and to record the initial value for state-diff generation). This read goes through `CachedState::get_compiled_class_hash`, which calls `SyncStateReader::get_compiled_class_hash`, which panics.

By contrast, the RPC execution path uses `ExecutionStateReader`, which has a complete implementation of `get_compiled_class_hash`: [5](#0-4) 

The gateway's `GenesisStateReader` also has a complete (default-returning) implementation: [6](#0-5) 

Only `SyncStateReader` — the path taken for all non-genesis blocks — is missing the implementation.

### Impact Explanation

**High.** Any user who submits a `DeclareV2` or `DeclareV3` transaction to the gateway will trigger the `todo!()` panic inside the stateful validation task. In Tokio, a panic in a spawned task terminates that task with a `JoinError`; the gateway will surface this as an internal error and reject the transaction. Valid Declare transactions are therefore permanently rejected at the gateway admission stage before they can reach the mempool or sequencer. This matches the allowed impact: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

### Likelihood Explanation

**High.** Submitting a `DeclareV2` or `DeclareV3` transaction is a standard, unprivileged operation available to any Starknet user. No special access or knowledge of the node internals is required. The code path is exercised on every such submission.

### Recommendation

Implement `SyncStateReader::get_compiled_class_hash` following the same pattern as `ExecutionStateReader::get_compiled_class_hash`: query the class manager or state-sync client for the compiled class hash, returning `CompiledClassHash::default()` for Cairo 0 / undeclared classes, as the trait contract requires.

### Proof of Concept

1. Start the Apollo sequencer with the gateway configured to use `SyncStateReaderFactory` (the default non-genesis configuration).
2. Submit a `DeclareV2` or `DeclareV3` transaction via the HTTP gateway endpoint.
3. The gateway calls `SyncStateReaderFactory::get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block`, obtaining a `SyncOrGenesisStateReader::Sync(SyncStateReader)`.
4. The blockifier wraps this in `CachedState` and executes the Declare transaction for validation.
5. During execution, `CachedState::get_compiled_class_hash` is called for the declared class hash; the cache is empty, so it calls `SyncStateReader::get_compiled_class_hash`.
6. `todo!()` panics: `"not yet implemented"`.
7. The validation task terminates; the gateway returns an internal error; the valid Declare transaction is rejected.

### Citations

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L197-199)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        todo!()
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L349-352)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        Ok(CompiledClassHash::default())
    }
}
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L443-450)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        match self {
            Self::Sync(state_reader) => state_reader.get_compiled_class_hash(class_hash),
            Self::Genesis(genesis_state_reader) => {
                genesis_state_reader.get_compiled_class_hash(class_hash)
            }
        }
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L518-550)
```rust
impl StateReaderFactory for SyncStateReaderFactory {
    type TGatewayStateReaderWithCompiledClasses = SyncOrGenesisStateReader;
    type TGatewayFixedBlockStateReader = SyncOrGenesisFixedBlockStateReader;

    // TODO(guy.f): The call to `get_latest_block_number()` is not counted in the storage metrics as
    // it is done prior to the creation of SharedStateSyncClientMetricWrapper, directly via the
    // SharedStateSyncClient.
    async fn get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block(
        &self,
    ) -> StateSyncClientResult<(
        Self::TGatewayStateReaderWithCompiledClasses,
        Self::TGatewayFixedBlockStateReader,
    )> {
        let latest_block_number = self.shared_state_sync_client.get_latest_block_number().await?;

        // If no blocks exist yet, return genesis state readers for bootstrap transactions.
        let Some(latest_block_number) = latest_block_number else {
            info!("No blocks found yet; using genesis state readers for bootstrap transactions.");
            return Ok((GenesisStateReader.into(), GenesisFixedBlockStateReader.into()));
        };

        let blockifier_state_reader = SyncStateReader::from_number(
            self.shared_state_sync_client.clone(),
            self.class_manager_client.clone(),
            latest_block_number,
            self.runtime.clone(),
        );
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
        Ok((blockifier_state_reader.into(), gateway_fixed_block_sync_state_client.into()))
    }
```

**File:** crates/blockifier/src/state/cached_state.rs (L204-216)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let mut cache = self.cache.borrow_mut();

        if cache.get_compiled_class_hash(class_hash).is_none() {
            let compiled_class_hash = self.state.get_compiled_class_hash(class_hash)?;
            cache.set_compiled_class_hash_initial_value(class_hash, compiled_class_hash);
        }

        let compiled_class_hash = cache
            .get_compiled_class_hash(class_hash)
            .unwrap_or_else(|| panic!("Cannot retrieve '{class_hash:?}' from the cache."));
        Ok(*compiled_class_hash)
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-208)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        if let Some(pending_data) = &self.maybe_pending_data {
            for DeclaredClassHashEntry { class_hash: other_class_hash, compiled_class_hash } in
                &pending_data.declared_classes
            {
                if class_hash == *other_class_hash {
                    return Ok(*compiled_class_hash);
                }
            }
        }

        let maybe_block_number = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_reader()
            .map_err(storage_err_to_state_err)?
            .get_class_definition_block_number(&class_hash)
            .map_err(storage_err_to_state_err)?;

        // Cairo 0 classes (and undeclared classes) do not have a compiled class hash.
        // According to the trait, return the default value.
        let Some(block_number) = maybe_block_number else {
            return Ok(CompiledClassHash::default());
        };

        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
    }
```
