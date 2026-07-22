### Title
Unauthenticated `RemoteComponentServer` Exposes All Batcher Operations to Any Network-Reachable Caller, Enabling Arbitrary State Injection via `AddSyncBlock` - (File: `crates/apollo_infra/src/component_server/remote_component_server.rs`)

### Summary
The `RemoteComponentServer` used by the batcher in distributed deployments has no authentication mechanism. Any network-reachable entity can send any `BatcherRequest` — including `AddSyncBlock`, `RevertBlock`, and `DecisionReached` — directly to the batcher's HTTP port. The `add_sync_block` handler accepts a caller-supplied `SyncBlock` containing an arbitrary `state_diff` and commits it to storage after only a height-equality check, which is trivially satisfied by first querying `GetCurrentHeight`. This allows an attacker to write arbitrary storage values, nonces, class hashes, and L1 transaction records into the committed chain state.

### Finding Description

**Root cause — no authentication in `RemoteComponentServer`:**

`remote_component_server_handler` in `crates/apollo_infra/src/component_server/remote_component_server.rs` deserializes the incoming HTTP body and forwards it to the local component client with zero caller verification:

```rust
let response = tokio::spawn(async move { local_client.send(request).await })
    .await
    .expect("Should be able to extract value from the task");
```

There is no token check, mTLS, IP allowlist, or any other access control. [1](#0-0) 

**Exposure surface — batcher binds to `0.0.0.0` in distributed mode:**

The production distributed deployment config sets:
```json
"components.batcher.remote_server_config.bind_ip": "0.0.0.0"
``` [2](#0-1) 

This means the batcher's `RemoteBatcherServer` listens on every network interface. The full `BatcherRequest` enum — including `AddSyncBlock`, `RevertBlock`, `DecisionReached`, `ProposeBlock`, `StartHeight` — is reachable from any host that can open a TCP connection to that port. [3](#0-2) 

**Exploitable path — `add_sync_block` commits attacker-supplied state:**

`Batcher::add_sync_block` has exactly one guard: the `block_number` field of the supplied `SyncBlock` must equal the current storage height. After that check passes, it calls `commit_proposal_and_block` with the attacker-controlled `state_diff` verbatim:

```rust
let height = self.get_height_from_storage()?;
if height != block_number {
    return Err(BatcherError::StorageHeightMarkerMismatch { ... });
}
// ... aborts any active proposal ...
self.commit_proposal_and_block(
    height,
    state_diff.clone(),   // ← attacker-supplied, no cryptographic validation
    address_to_nonce,
    l1_transaction_hashes.iter().copied().collect(),
    Default::default(),
    storage_commitment_block_hash,
).await?;
``` [4](#0-3) 

The `block_header_commitments` field (which becomes `StorageCommitmentBlockHash::Partial`) is also attacker-supplied and is not validated against the `state_diff`. [5](#0-4) 

Additionally, `revert_block` has only a height-equality guard and no authentication, allowing an attacker to roll back committed blocks. [6](#0-5) 

### Impact Explanation

An attacker who can reach the batcher's TCP port can:

1. Call `GetCurrentHeight` to learn height H.
2. Craft a `SyncBlock` with `block_number = H`, an arbitrary `state_diff` (arbitrary storage slots, nonces, class hashes, L1 handler records), and arbitrary `block_header_commitments`.
3. Call `AddSyncBlock`. The batcher aborts any in-progress proposal and commits the fake block to persistent storage.

The result is **wrong storage values, wrong nonces, wrong class hashes, and wrong L1 message records** written into the canonical chain state — matching the Critical impact: *"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."*

A separate attack using `RevertBlock` can silently undo already-committed blocks, corrupting the chain tip.

### Likelihood Explanation

The batcher's remote server is bound to `0.0.0.0` in the production distributed deployment config. No authentication exists at the code level. The only prerequisite is network reachability to the batcher's port — a condition met by any co-tenant in the same Kubernetes cluster, any compromised sidecar, or any misconfigured network policy. The current height needed to pass the height-equality guard is freely obtainable via `GetCurrentHeight` on the same unauthenticated port.

### Recommendation

1. **Add mutual TLS (mTLS)** to `RemoteComponentServer` so only components holding a valid certificate can connect.
2. **Alternatively, add a shared-secret bearer token** checked in `remote_component_server_handler` before forwarding the request.
3. **Restrict `bind_ip`** to the loopback or a dedicated internal interface rather than `0.0.0.0`.
4. **Add a caller-identity check** inside `add_sync_block` and `revert_block` to ensure they are only callable from the state-sync or consensus-orchestrator component.

### Proof of Concept

```
# Step 1: discover current height (unauthenticated)
POST http://<batcher-host>:<port>/
Body: BatcherRequest::GetCurrentHeight
→ Response: GetHeightResponse { height: H }

# Step 2: craft a SyncBlock that sets an arbitrary storage slot
SyncBlock {
    block_header_without_hash: BlockHeaderWithoutHash {
        block_number: H,
        starknet_version: StarknetVersion::LATEST,
        parent_hash: <any>,
        ...
    },
    state_diff: ThinStateDiff {
        storage_diffs: { <victim_contract>: { <slot>: <attacker_value> } },
        nonces: { <victim_account>: Nonce(0) },  // reset nonce
        ...
    },
    block_header_commitments: Some(BlockHeaderCommitments::default()),
    account_transaction_hashes: [],
    l1_transaction_hashes: [],
}

# Step 3: inject
POST http://<batcher-host>:<port>/
Body: BatcherRequest::AddSyncBlock(crafted_sync_block)
→ Response: Ok(())
# Batcher aborts any active proposal and commits the fake state to storage.
```

The `state_diff` is written directly to the MDBX storage via `commit_proposal_and_block` with no cryptographic check against the supplied `block_header_commitments`. [7](#0-6)

### Citations

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L217-235)
```rust
        let http_response = match SerdeWrapper::<Request>::wrapper_deserialize(&body_bytes)
            .map_err(|err| ClientError::ResponseDeserializationFailure(err.to_string()))
        {
            Ok(request) => {
                trace!(
                    remote_addr = %client_peer,
                    request_id = %request_id,
                    request_type = request.request_label(),
                    "remote component request",
                );
                trace!("Successfully deserialized request: {request:?}");
                metrics.increment_valid_received();

                // Wrap the send operation in a tokio::spawn as it is NOT a cancel-safe operation.
                // Even if the current task is cancelled, the inner task will continue to run.
                // Note: this creates a new request ID for the local client.
                let response = tokio::spawn(async move { local_client.send(request).await })
                    .await
                    .expect("Should be able to extract value from the task");
```

**File:** crates/apollo_deployments/resources/services/distributed/batcher.json (L15-17)
```json
  "components.batcher.remote_server_config.#is_none": false,
  "components.batcher.remote_server_config.bind_ip": "0.0.0.0",
  "components.batcher.remote_server_config.max_streams_per_connection": 8,
```

**File:** crates/apollo_batcher_types/src/communication.rs (L117-134)
```rust
pub enum BatcherRequest {
    ProposeBlock(ProposeBlockInput),
    GetBlockHash(BlockNumber),
    #[cfg(feature = "os_input")]
    GetStateCommitmentInfos(BlockNumber),
    GetProposalContent(GetProposalContentInput),
    ValidateBlock(ValidateBlockInput),
    AbortProposal(ProposalId),
    FinishProposal(FinishProposalInput),
    SendTxsForProposal(SendTxsForProposalInput),
    StartHeight(StartHeightInput),
    GetCurrentHeight,
    DecisionReached(DecisionReachedInput),
    AddSyncBlock(SyncBlock),
    RevertBlock(RevertBlockInput),
    GetBatchTimestamp,
    CallContract(CallContractInput),
}
```

**File:** crates/apollo_batcher/src/batcher.rs (L834-911)
```rust
        let height = self.get_height_from_storage()?;
        if height != block_number {
            return Err(BatcherError::StorageHeightMarkerMismatch {
                marker_height: height,
                requested_height: block_number,
            });
        }

        if let Some(height) = self.active_height {
            info!("Aborting all work on height {} due to state sync.", height);
            self.abort_active_height().await;
        }

        let address_to_nonce = state_diff.nonces.iter().map(|(k, v)| (*k, *v)).collect();

        let storage_commitment_block_hash = if block_header_without_hash
            .starknet_version
            .has_partial_block_hash_components()
        {
            self.maybe_handle_first_block_with_partial_block_hash(
                block_header_without_hash.parent_hash,
                block_number,
            )
            .map_err(|err| {
                error!("Error handling block number {block_number} with partial block hash: {err}");
                BatcherError::InternalError
            })?;
            match block_header_commitments {
                Some(header_commitments) => {
                    StorageCommitmentBlockHash::Partial(PartialBlockHashComponents {
                        header_commitments,
                        block_number,
                        l1_gas_price: block_header_without_hash.l1_gas_price,
                        l1_data_gas_price: block_header_without_hash.l1_data_gas_price,
                        l2_gas_price: block_header_without_hash.l2_gas_price,
                        sequencer: block_header_without_hash.sequencer,
                        timestamp: block_header_without_hash.timestamp,
                        starknet_version: block_header_without_hash.starknet_version,
                    })
                }
                None => return Err(BatcherError::MissingHeaderCommitments { block_number }),
            }
        } else {
            let first_block_with_partial_block_hash_number = self
                .config
                .static_config
                .first_block_with_partial_block_hash
                .as_ref()
                .expect(
                    "Since an old block was learned via sync, first block with partial block hash \
                     components should be configured.",
                )
                .block_number;
            assert!(
                height < first_block_with_partial_block_hash_number,
                "Height {height} is at least the first block configured to include a partial hash \
                 ({first_block_with_partial_block_hash_number}) but does not include one.",
            );
            StorageCommitmentBlockHash::ParentHash(block_header_without_hash.parent_hash)
        };

        let optional_state_diff_commitment = match &storage_commitment_block_hash {
            StorageCommitmentBlockHash::ParentHash(_) => None,
            StorageCommitmentBlockHash::Partial(PartialBlockHashComponents {
                ref header_commitments,
                ..
            }) => Some(header_commitments.state_diff_commitment),
        };

        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            address_to_nonce,
            l1_transaction_hashes.iter().copied().collect(),
            Default::default(),
            storage_commitment_block_hash,
        )
        .await?;
```

**File:** crates/apollo_batcher/src/batcher.rs (L1358-1386)
```rust
    pub async fn revert_block(&mut self, input: RevertBlockInput) -> BatcherResult<()> {
        info!("Reverting block at height {}.", input.height);
        let height = self.get_height_from_storage()?.prev().ok_or(
            BatcherError::StorageHeightMarkerMismatch {
                marker_height: BlockNumber(0),
                requested_height: input.height,
            },
        )?;

        if height != input.height {
            return Err(BatcherError::StorageHeightMarkerMismatch {
                marker_height: height.unchecked_next(),
                requested_height: input.height,
            });
        }

        if let Some(height) = self.active_height {
            info!("Aborting all work on height {} due to a revert request.", height);
            self.abort_active_height().await;
        }

        // Wait for the revert commitment to be completed before reverting the storage.
        self.revert_commitment(height).await;

        self.storage_writer.revert_block(height);
        BUILDING_HEIGHT.decrement(1);
        GLOBAL_ROOT_HEIGHT.decrement(1);
        REVERTED_BLOCKS.increment(1);
        Ok(())
```
