### Title
`compare_retrospective_block_hash` Permanently Disabled in Production Silences the Retrospective Block Hash Consistency Guard - (File: `crates/apollo_consensus_orchestrator/src/utils.rs`)

### Summary

The `compare_retrospective_block_hash` boolean flag, which is intended to enforce that the batcher and state sync agree on the retrospective block hash before it is embedded in a validated block's execution context, is hardcoded to `false` in every production deployment configuration. The guard is declared, threaded through the entire proposal validation path, and checked at the critical comparison site — but because the flag is never `true` in production, the mismatch branch is unreachable. A divergence between the batcher's and state sync's view of the retrospective block hash is silently accepted, and the batcher's (potentially wrong) hash is forwarded to the blockifier as authoritative.

### Finding Description

`retrospective_block_hash()` in `crates/apollo_consensus_orchestrator/src/utils.rs` fetches the retrospective block hash from both the batcher and state sync, then guards the mismatch error behind the flag:

```rust
if compare_retrospective_block_hash && state_sync_block_hash != batcher_block_hash {
    // ... return Err(RetrospectiveBlockHashError::HashMismatch { ... })
}
Ok(Some(BlockHashAndNumber { number: block_number, hash: batcher_block_hash }))
``` [1](#0-0) 

When the flag is `false`, the function unconditionally returns `Ok(Some(...))` using the batcher's hash, regardless of what state sync reports. The returned hash is then passed directly into `ValidateBlockInput.retrospective_block_hash` inside `initiate_validation()`: [2](#0-1) 

The production deployment configuration sets this flag to `false`:

```json
"consensus_manager_config.context_config.dynamic_config.compare_retrospective_block_hash": false,
``` [3](#0-2) 

The same `false` default appears in the replacer deployment config as well. The flag is part of `ContextDynamicConfig`, which is the live-reloadable config struct, but no deployment overlay ever sets it to `true`. [4](#0-3) 

The retrospective block hash is the hash of block `height - STORED_BLOCK_HASH_BUFFER`. It is written into the `block_hash_contract_address` storage by the blockifier during execution, making it observable to any contract that calls the `get_block_hash` syscall. A wrong value here produces a wrong storage root and wrong syscall results for all contracts that query historical block hashes.

### Impact Explanation

The retrospective block hash is embedded in `BlockInfo` and used by the blockifier to populate the `block_hash_contract_address` contract's storage. If the batcher holds a diverged or corrupted hash (e.g., after a reorg, a storage bug, or a restart from a wrong snapshot), the validator accepts the proposal without detecting the discrepancy. The wrong hash is committed to state, corrupting the `get_block_hash` syscall return value for all contracts at that block height. This is a wrong storage value / wrong syscall result from accepted input — matching the "Wrong state … storage value … or revert result from blockifier/syscall/execution logic for accepted input" impact category.

### Likelihood Explanation

The flag has been `false` in every checked production config file. The guard is structurally present and tested (unit tests set it to `true`), but the production path never enables it. Any batcher/state-sync divergence — reorg, snapshot mismatch, restart from backup — silently propagates a wrong retrospective hash into committed state. No external attacker action is required; the condition can arise from ordinary operational events.

### Recommendation

Set `compare_retrospective_block_hash: true` in all production deployment configs (`consensus_manager_config.json`, `replacer_consensus_manager_config.json`, and any overlay that inherits from them). If the flag was intentionally disabled as a temporary measure, add an explicit comment and a tracking issue. Consider promoting it to a static (non-dynamic) config field so it cannot be silently left `false` in a new deployment.

### Proof of Concept

1. Deploy two nodes with the production config (`compare_retrospective_block_hash: false`).
2. Corrupt the batcher's stored hash for block `N - STORED_BLOCK_HASH_BUFFER` (e.g., by restoring from a wrong snapshot).
3. The proposer calls `wait_for_retrospective_block_hash` → flag is `false` → batcher's wrong hash is returned without cross-checking state sync.
4. The wrong hash is placed in `ProposeBlockInput.retrospective_block_hash` and streamed to validators.
5. Each validator calls `retrospective_block_hash` with `compare_retrospective_block_hash = false` → same wrong hash is accepted without error.
6. `ValidateBlockInput` carries the wrong hash into the blockifier; the `block_hash_contract_address` storage is written with the corrupted value.
7. Any contract calling `get_block_hash(N - STORED_BLOCK_HASH_BUFFER)` at or after this block receives the wrong value, and the state root is wrong. [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L355-397)
```rust
pub(crate) async fn retrospective_block_hash(
    batcher_client: Arc<dyn BatcherClient>,
    state_sync_client: SharedStateSyncClient,
    init: &ProposalInit,
    compare_retrospective_block_hash: bool,
) -> RetrospectiveBlockHashResult<Option<BlockHashAndNumber>> {
    if let Some(required_height) = (init.height.0 + 1).checked_sub(STORED_BLOCK_HASH_BUFFER) {
        // Just verify that the batcher has done calculating it.
        batcher_client.get_block_hash(BlockNumber(required_height)).await?;
    }

    let retrospective_block_number = init.height.0.checked_sub(STORED_BLOCK_HASH_BUFFER);

    let Some(block_number) = retrospective_block_number else {
        info!(
            "Retrospective block number is less than {STORED_BLOCK_HASH_BUFFER}, setting None as \
             expected."
        );
        return Ok(None);
    };

    let block_number = BlockNumber(block_number);

    // First try from state sync - assuming it takes longer to this one to be ready.
    let state_sync_block_hash = state_sync_client.get_block_hash(block_number).await?;

    // Then try from batcher.
    let batcher_block_hash = batcher_client.get_block_hash(block_number).await?;

    if compare_retrospective_block_hash && state_sync_block_hash != batcher_block_hash {
        warn!(
            "Retrospective block hashes mismatch for block {block_number}: state sync block hash: \
             {state_sync_block_hash:?}, batcher block hash: {batcher_block_hash:?}"
        );
        CONSENSUS_RETROSPECTIVE_BLOCK_HASH_MISMATCH.increment(1);
        return Err(RetrospectiveBlockHashError::HashMismatch {
            block_number,
            state_sync_block_hash,
            batcher_block_hash,
        });
    }
    Ok(Some(BlockHashAndNumber { number: block_number, hash: batcher_block_hash }))
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L162-171)
```rust
    initiate_validation(
        args.deps.batcher.clone(),
        args.deps.state_sync_client,
        &args.init,
        args.proposal_id,
        args.timeout + args.batcher_timeout_margin,
        args.deps.clock.as_ref(),
        args.compare_retrospective_block_hash,
    )
    .await?;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-467)
```rust
    let input = ValidateBlockInput {
        proposal_id,
        deadline: clock.now() + chrono_timeout,
        retrospective_block_hash: retrospective_block_hash(
            batcher.clone(),
            state_sync_client,
            init,
            compare_retrospective_block_hash,
        )
        .await
        .map_err(ValidateProposalError::from)?,
        block_info: convert_to_sn_api_block_info(init)?,
    };
```

**File:** crates/apollo_deployments/resources/app_configs/consensus_manager_config.json (L60-60)
```json
  "consensus_manager_config.context_config.dynamic_config.compare_retrospective_block_hash": false,
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L136-142)
```rust
#[derive(Debug, Default, Deserialize, Serialize, Clone, PartialEq, Validate)]
pub struct ContextConfig {
    #[validate(nested)]
    pub dynamic_config: ContextDynamicConfig,
    #[validate(nested)]
    pub static_config: ContextStaticConfig,
}
```
