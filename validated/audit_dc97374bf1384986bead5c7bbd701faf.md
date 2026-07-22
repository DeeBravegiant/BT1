### Title
Validator's Bouncer Weights Not Truncated on `close_block(final_n_executed_txs)` — Inflated Resource Accounting Propagates to Proving Blob - (File: `crates/blockifier/src/blockifier/concurrent_transaction_executor.rs`)

---

### Summary

When a validator node executes more transactions than the proposer ultimately included in the final block (`n_committed_txs > final_n_executed_txs`), `ConcurrentTransactionExecutor::close_block` correctly truncates the **state** to the first `final_n_executed_txs` transactions but passes the **full, untruncated bouncer** to `finalize_block`. The bouncer has accumulated weights for all `n_committed_txs` committed transactions. The resulting `BlockExecutionSummary` — and every downstream artifact — carries inflated `bouncer_weights`, `casm_hash_computation_data_sierra_gas`, `casm_hash_computation_data_proving_gas`, and `compiled_class_hashes_for_migration` that include contributions from transactions that are **not in the final block**.

---

### Finding Description

**Root cause — `close_block` in `ConcurrentTransactionExecutor`:**

```
commit_chunk_and_recover_block_state(final_n_executed_txs)  // state truncated ✓
finalize_block(&worker_executor.bouncer, ...)               // bouncer NOT truncated ✗
```

The `bouncer` is a shared `Arc<Mutex<Bouncer>>` that is updated inside `commit_tx` for every transaction that passes the bouncer check. By the time `close_block(N)` is called, the bouncer has accumulated weights for all `n_committed_txs` transactions (which may be `N + K`, `K > 0`). `finalize_block` reads `bouncer.get_bouncer_weights()` and `casm_hash_computation_data_*` from this full bouncer, so the returned `BlockExecutionSummary` reflects `N + K` transactions, not `N`. [1](#0-0) 

**Propagation chain:**

1. `build_block_inner` calls `close_block(final_n_executed_txs_nonopt)` → gets inflated `block_summary`.
2. `execution_data.remove_last_txs(...)` correctly removes the K extra transactions from `execution_infos_and_signatures`, `rejected_tx_hashes`, `consumed_l1_handler_tx_hashes`. The `l2_gas_used()` recomputed from the trimmed `execution_data` is therefore correct.
3. `BlockExecutionArtifacts::new(block_summary, execution_data, ...)` stores the inflated `bouncer_weights`, `casm_hash_computation_data_sierra_gas`, `casm_hash_computation_data_proving_gas`, `compiled_class_hashes_for_migration` from `block_summary` unchanged. [2](#0-1) [3](#0-2) 

4. `batcher::decision_reached` reads these fields directly from `block_execution_artifacts` and places them into `DecisionReachedResponse.central_objects`. [4](#0-3) 

5. `finalize_decision` passes `central_objects.bouncer_weights`, `casm_hash_computation_data_sierra_gas`, `casm_hash_computation_data_proving_gas`, and `compiled_class_hashes_for_migration` verbatim into `BlobParameters` for `prepare_blob_for_next_height`. [5](#0-4) 

6. `AerospikeBlob::from_blob_parameters_and_class_manager` embeds these wrong values into the blob that is later written to Aerospike and consumed by the OS runner. [6](#0-5) 

---

### Impact Explanation

The `AerospikeBlob` prepared by a validator node (when it subsequently acts as proposer for the next block) contains:

- **Inflated `bouncer_weights`** (`sierra_gas`, `proving_gas`, `receipt_l2_gas`, `state_diff_size`, `n_events`, `l1_gas`) — includes resource consumption from K transactions that are not in the committed block.
- **Inflated `casm_hash_computation_data_sierra_gas` / `_proving_gas`** — includes CASM hash computation costs for classes first loaded by the K extra transactions.
- **Wrong `compiled_class_hashes_for_migration`** — may include class hashes that were only touched by the K extra transactions.

The OS runner uses these values to replay and prove the block. Wrong proving-gas figures cause the OS to compute incorrect resource bounds for the block, which can cause proof generation to fail or produce a proof that does not match the on-chain state. This is an incorrect bouncer/resource-accounting value with direct economic impact on the proving pipeline.

The block hash, state diff, nonces, fees, and `l2_gas_consumed` in the block header are **not** affected (they are derived from the correctly truncated `execution_data` and state).

---

### Likelihood Explanation

The trigger condition — validator executes more transactions than `final_n_executed_txs` — is explicitly documented in the code as a normal, expected scenario:

> *"This can happen if the proposer sends some transactions but closes the block before including them, while the validator already executed those transactions."* [7](#0-6) 

With concurrent execution (`n_concurrent_txs > 1`), the validator will routinely have in-flight transactions beyond `final_n_executed_txs` at the moment the Fin signal arrives. Any proposer that closes the block while the validator still has transactions in the pipeline triggers this path. No special privileges are required.

---

### Recommendation

In `ConcurrentTransactionExecutor::close_block`, the bouncer must reflect only the first `final_n_executed_txs` transactions. Two approaches:

1. **Per-transaction weight tracking**: Record each transaction's marginal `TxWeights` at commit time (indexed by `TxIndex`). In `close_block`, reconstruct the bouncer by summing only the first `final_n_executed_txs` entries before calling `finalize_block`.

2. **Snapshot approach**: Before committing transactions beyond `final_n_executed_txs`, snapshot the bouncer state at index `final_n_executed_txs` and pass that snapshot to `finalize_block` instead of the live bouncer.

The fix mirrors the existing pattern for state truncation: just as `commit_chunk_and_recover_block_state(final_n_executed_txs)` applies only the first N writes to the state, the bouncer should be reconstructed from only the first N transactions' weights. [8](#0-7) 

---

### Proof of Concept

1. Proposer builds a block with N = 10 transactions; sends `ProposalFin` with `executed_transaction_count = 10`.
2. Validator, running with `n_concurrent_txs = 4`, has already submitted transactions 11–13 to the executor before the Fin signal arrives. All 13 are committed by the scheduler (`n_committed_txs = 13`).
3. `build_block_inner` receives `final_n_executed_txs = 10`. The loop exits because `n_executed_txs (13) >= final_n_executed_txs (10)`.
4. `close_block(10)` is called:
   - `commit_chunk_and_recover_block_state(10)` → state reflects only txs 1–10. ✓
   - `finalize_block(&bouncer, ...)` → bouncer holds accumulated weights for txs 1–13. ✗
   - `BlockExecutionSummary.bouncer_weights.sierra_gas` = sum of sierra gas for 13 txs (should be 10).
5. `remove_last_txs([tx11, tx12, tx13])` removes them from `execution_data`. `l2_gas_used()` = sum for 10 txs. ✓
6. `BlockExecutionArtifacts.bouncer_weights.sierra_gas` = 13-tx value. ✗
7. `decision_reached` returns this in `CentralObjects.bouncer_weights`.
8. If this validator node is the proposer for block N+1, `finalize_decision` calls `prepare_blob_for_next_height` with the 13-tx `bouncer_weights` and `casm_hash_computation_data`.
9. The blob written to Aerospike for block N contains wrong proving-gas figures. The OS runner, which uses these to verify resource bounds, encounters a mismatch and either rejects the block or produces an invalid proof. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L124-146)
```rust
    pub fn close_block(
        &mut self,
        final_n_executed_txs: usize,
    ) -> TransactionExecutorResult<BlockExecutionSummary> {
        log::info!("Worker executor: Closing block.");
        let worker_executor = &self.worker_executor;
        worker_executor.scheduler.halt();

        let n_committed_txs = worker_executor.scheduler.get_n_committed_txs();
        assert!(
            final_n_executed_txs <= n_committed_txs,
            "Close block requested with {final_n_executed_txs} transactions, but only \
             {n_committed_txs} transactions were committed."
        );

        let mut state_after_block =
            worker_executor.commit_chunk_and_recover_block_state(final_n_executed_txs);
        finalize_block(
            &worker_executor.bouncer,
            &mut state_after_block,
            &self.worker_executor.block_context,
        )
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L147-195)
```rust
impl BlockExecutionArtifacts {
    pub async fn new(
        block_summary: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
        #[cfg(feature = "os_input")]
        let initial_reads = block_summary.initial_reads;
        let BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
            // TODO(Yoav): Remove the ".." when the os_input feature is removed.
            ..
        } = block_summary;
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            #[cfg(feature = "os_input")]
            initial_reads,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L393-401)
```rust
        let final_n_executed_txs_nonopt = if self.execution_params.is_validator {
            final_n_executed_txs.expect("final_n_executed_txs must be set in validate mode.")
        } else {
            assert!(
                final_n_executed_txs.is_none(),
                "final_n_executed_txs must be None in propose mode."
            );
            self.n_executed_txs
        };
```

**File:** crates/apollo_batcher/src/block_builder.rs (L451-461)
```rust
        let mut execution_data = std::mem::take(&mut self.execution_data);
        if let Some(final_n_executed_txs) = final_n_executed_txs {
            // Remove the transactions that were executed, but eventually not included in the block.
            // This can happen if the proposer sends some transactions but closes the block before
            // including them, while the validator already executed those transactions.
            let remove_tx_hashes: Vec<TransactionHash> =
                self.block_txs[final_n_executed_txs..].iter().map(|tx| tx.tx_hash()).collect();
            execution_data.remove_last_txs(&remove_tx_hashes);
        }
        Ok(BlockExecutionArtifacts::new(block_summary, execution_data, final_n_executed_txs_nonopt)
            .await)
```

**File:** crates/apollo_batcher/src/batcher.rs (L1017-1040)
```rust
        SIERRA_GAS_IN_LAST_BLOCK.set_lossy(block_execution_artifacts.bouncer_weights.sierra_gas.0);
        PROVING_GAS_IN_LAST_BLOCK
            .set_lossy(block_execution_artifacts.bouncer_weights.proving_gas.0);
        L2_GAS_IN_LAST_BLOCK.set_lossy(block_execution_artifacts.l2_gas_used.0);

        Ok(DecisionReachedResponse {
            state_diff,
            central_objects: CentralObjects {
                execution_infos,
                bouncer_weights: block_execution_artifacts.bouncer_weights,
                compressed_state_diff: block_execution_artifacts.compressed_state_diff,
                casm_hash_computation_data_sierra_gas: block_execution_artifacts
                    .casm_hash_computation_data_sierra_gas,
                casm_hash_computation_data_proving_gas: block_execution_artifacts
                    .casm_hash_computation_data_proving_gas,
                compiled_class_hashes_for_migration: block_execution_artifacts
                    .compiled_class_hashes_for_migration,
                parent_proposal_commitment,
                #[cfg(feature = "os_input")]
                accessed_keys,
                #[cfg(feature = "os_input")]
                initial_reads,
            },
        })
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L590-631)
```rust
        if let Err(e) = self
            .deps
            .cende_ambassador
            .prepare_blob_for_next_height(BlobParameters {
                block_info: cende_block_info,
                state_diff,
                compressed_state_diff: central_objects.compressed_state_diff,
                transactions_with_execution_infos,
                bouncer_weights: central_objects.bouncer_weights,
                casm_hash_computation_data_sierra_gas: central_objects
                    .casm_hash_computation_data_sierra_gas,
                casm_hash_computation_data_proving_gas: central_objects
                    .casm_hash_computation_data_proving_gas,
                fee_market_info: FeeMarketInfo {
                    l2_gas_consumed: l2_gas_used,
                    next_l2_gas_price: self.l2_gas_price,
                },
                // Forward the proposer's stated fee_proposal_fri (from ProposalInit)
                // to the centralized cende pipeline. The centralized side persists this in
                // its own storage namespace, separate from FeeMarketInfo. Pre-V0_14_3 blocks
                // have `init.fee_proposal_fri == None`.
                fee_proposal_info: FeeProposalInfo { fee_proposal_fri: init.fee_proposal_fri },
                compiled_class_hashes_for_migration: central_objects
                    .compiled_class_hashes_for_migration,
                proposal_commitment: commitment,
                parent_proposal_commitment: central_objects
                    .parent_proposal_commitment
                    .map(|c| proposal_commitment_from(c.partial_block_hash, parent_fee_proposal)),
                recent_block_hashes: self.collect_recent_block_hashes(height).await,
                #[cfg(feature = "os_input")]
                recent_state_commitment_infos: self
                    .collect_recent_state_commitment_infos(height)
                    .await,
                #[cfg(feature = "os_input")]
                accessed_keys: central_objects.accessed_keys,
                #[cfg(feature = "os_input")]
                initial_reads: central_objects.initial_reads,
            })
            .await
        {
            error!("Failed to prepare blob for next height at height {height}: {e:?}");
        }
```

**File:** crates/apollo_consensus_orchestrator/src/cende/mod.rs (L449-475)
```rust
        Ok(AerospikeBlob {
            block_number,
            state_diff,
            compressed_state_diff,
            bouncer_weights: blob_parameters.bouncer_weights.into(),
            fee_market_info: blob_parameters.fee_market_info,
            fee_proposal_info: blob_parameters.fee_proposal_info,
            transactions: central_transactions,
            execution_infos,
            contract_classes,
            compiled_classes,
            casm_hash_computation_data_sierra_gas: blob_parameters
                .casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas: blob_parameters
                .casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration: blob_parameters
                .compiled_class_hashes_for_migration,
            proposal_commitment: blob_parameters.proposal_commitment,
            parent_proposal_commitment: blob_parameters.parent_proposal_commitment,
            recent_block_hashes: blob_parameters.recent_block_hashes,
            #[cfg(feature = "os_input")]
            recent_state_commitment_infos: blob_parameters.recent_state_commitment_infos,
            #[cfg(feature = "os_input")]
            accessed_keys: blob_parameters.accessed_keys,
            #[cfg(feature = "os_input")]
            initial_reads: blob_parameters.initial_reads,
        })
```
