### Title
`SyncStateReader::get_compiled_class_hash()` Implemented as `todo!()` Causes Gateway Panic on Declare Transaction Validation — (`File: crates/apollo_gateway/src/sync_state_reader.rs`)

### Summary

`SyncStateReader` implements the `BlockifierStateReader` trait but leaves `get_compiled_class_hash()` as `todo!()`. The blockifier calls this method on the underlying state reader during Declare transaction processing (to check whether a class is already declared). Any Declare transaction submitted through the gateway triggers this code path, causing a runtime panic that crashes the gateway task and renders Declare transaction admission permanently broken.

### Finding Description

`SyncStateReader` is the production state reader injected into the gateway for stateful transaction validation. It implements `blockifier::state::state_api::StateReader` but its `get_compiled_class_hash` method body is:

```rust
// crates/apollo_gateway/src/sync_state_reader.rs, line 197-199
fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    todo!()
}
``` [1](#0-0) 

The `BlockifierStateReader` trait declares `get_compiled_class_hash` as a required method with no default implementation:

```rust
// crates/blockifier/src/state/state_api.rs, line 44-46
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash>;
``` [2](#0-1) 

The `CachedState<S: StateReader>` wraps `SyncStateReader` and delegates cache misses to the underlying reader. During Declare transaction execution the blockifier calls `get_compiled_class_hash` on the `CachedState` to verify the class is not already declared; on a cache miss this falls through to `SyncStateReader::get_compiled_class_hash`, hitting `todo!()` and panicking. [3](#0-2) 

The `SyncStateReaderFactory` constructs a `SyncStateReader` and wraps it in `SyncOrGenesisStateReader`, which is the concrete type used for all stateful gateway validation: [4](#0-3) 

`SyncOrGenesisStateReader::get_compiled_class_hash` delegates directly to `SyncStateReader::get_compiled_class_hash`: [5](#0-4) 

### Impact Explanation

Every Declare transaction submitted to the gateway reaches stateful validation, which creates a `CachedState<SyncOrGenesisStateReader>` and runs the blockifier. The blockifier's Declare execution path calls `get_compiled_class_hash` to check for prior declaration. Because the cache is cold for a freshly submitted class, the call falls through to `SyncStateReader::get_compiled_class_hash`, which panics unconditionally via `todo!()`. This crashes the gateway validation task for every Declare transaction, making it impossible to declare any new contract class through the gateway. The impact matches: **High — gateway admission rejects valid transactions before sequencing**.

### Likelihood Explanation

Any unprivileged user can submit a `DeclareTransactionV3` to the gateway RPC endpoint. No special permissions, keys, or network position are required. The panic is deterministic and 100% reproducible for every Declare transaction.

### Recommendation

Implement `get_compiled_class_hash` in `SyncStateReader` by querying the state sync client (analogous to how `get_class_hash_at` and `get_nonce_at` are implemented), or by returning `CompiledClassHash::default()` if the class is not found — matching the documented contract ("Returns `CompiledClassHash::default()` if no v1_class is found"):

```rust
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    // Query state sync or class manager for the compiled class hash.
    // Return CompiledClassHash::default() if not found.
    todo!("implement via state_sync_client or class_manager_client")
}
```

Additionally, add an integration test that submits a Declare transaction through the gateway to prevent regression.

### Proof of Concept

1. Start the Apollo sequencer node with the gateway enabled.
2. Construct a valid `DeclareTransactionV3` with a new Sierra class.
3. Submit it to the gateway's `starknet_addDeclareTransaction` RPC endpoint.
4. The gateway's stateful validation creates `CachedState<SyncOrGenesisStateReader>`.
5. The blockifier calls `get_compiled_class_hash(class_hash)` on the cache; cache miss delegates to `SyncStateReader::get_compiled_class_hash`.
6. `todo!()` fires → `PanicInfo: not yet implemented` → the validation task panics.
7. The gateway returns an error to the caller and the Declare transaction is never admitted to the mempool.

### Citations

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L197-199)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        todo!()
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

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L525-550)
```rust
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

**File:** crates/blockifier/src/state/state_api.rs (L44-46)
```rust
    /// Returns the compiled class hash of the given class hash.
    /// Returns CompiledClassHash::default() if no v1_class is found for the given class hash.
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash>;
```

**File:** crates/blockifier/src/state/cached_state.rs (L73-80)
```rust
    /// Updates cache with initial cell values for write-only access.
    /// If written values match the original, the cell is unchanged and not counted as a
    /// storage-change for fee calculation.
    /// Note: in valid flows, all other read mappings must be filled at this point:
    ///   * Nonce: read previous before incrementing.
    ///   * Class hash: Deploy: verify the address is not occupied; Replace class: verify the
    ///     contract is deployed before running any code.
    ///   * Compiled class hash: verify the class is not declared through `get_compiled_class`.
```
