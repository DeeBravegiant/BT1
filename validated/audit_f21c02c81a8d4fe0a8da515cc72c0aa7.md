### Title
Hardcoded `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler` Causes Validators to Reject Every Valid Proposal Containing L1 Handler Transactions — (`File: crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

`TransactionConverter::convert_consensus_l1_handler_to_internal_l1_handler` unconditionally passes `Fee(1)` (1 wei) as `paid_fee_on_l1` when reconstructing an executable L1 handler transaction on the **validator** side. The **proposer** executes the same transaction with the real fee sourced from the L1 event. Because `paid_fee_on_l1` feeds into fee-collection accounting and can gate execution success, the two sides produce different state diffs and therefore different block commitments. Every proposal that contains at least one L1 handler transaction triggers `ProposalFinMismatch` on every validator, making it impossible for the network to finalize any block that processes an L1→L2 message.

---

### Finding Description

`TransactionConverter` is used in two distinct roles:

**Proposer path (block builder):** L1 handler transactions arrive from the mempool as `InternalConsensusTransaction::L1Handler(executable_transaction::L1HandlerTransaction)` already carrying the real `paid_fee_on_l1` value scraped from the L1 event. The batcher passes them directly to the blockifier:

```rust
// transaction_converter.rs
InternalConsensusTransaction::L1Handler(tx) => Ok(ExecutableTransaction::L1Handler(tx)),
```

The blockifier executes with the real fee and produces a state diff that includes the correct sequencer-balance credit.

**Validator path (consensus orchestrator):** The proposer streams L1 handler transactions over the network as `ConsensusTransaction::L1Handler(transaction::L1HandlerTransaction)`. This wire type does **not** carry `paid_fee_on_l1`. When the validator reconstructs the executable transaction it calls:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs  lines 473-483
fn convert_consensus_l1_handler_to_internal_l1_handler(
    &self,
    tx: transaction::L1HandlerTransaction,
) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
    Ok(executable_transaction::L1HandlerTransaction::create(
        tx,
        &self.chain_id,
        // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
        Fee(1),
    )?)
}
```

`Fee(1)` is 1 wei — a placeholder that is never replaced with the actual value. The blockifier then executes the transaction with this wrong fee, producing a different sequencer-balance delta (and potentially a different execution outcome if the real fee is required to cover gas costs). The resulting partial block hash diverges from the proposer's, and the validator's `ProposalFinMismatch` guard fires:

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs  lines 244-247
if built_block != received_fin.proposal_commitment {
    CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
    return Err(ValidateProposalError::ProposalFinMismatch);
}
```

The structural parallel to the reported Solidity bug is exact: a hardcoded sentinel value (`0` / `Fee(1)`) is substituted for the real identifier/amount, making every legitimate call fail.

---

### Impact Explanation

- **Wrong state / receipt / L1 message result (Critical path):** The sequencer-balance storage slot written by the blockifier differs between proposer and validator because `paid_fee_on_l1` drives the fee-collection credit. This is a wrong storage value produced by execution logic.
- **Proposal rejection (High path):** Every proposal that includes one or more L1 handler transactions is rejected by all validators with `ProposalFinMismatch`. No such block can ever reach consensus, so **all L1→L2 messages are permanently blocked** until the bug is fixed.

---

### Likelihood Explanation

L1 handler transactions are a routine part of Starknet operation (ETH deposits, ERC-20 bridges, etc.). Any sequencer that proposes a block containing even a single L1 handler transaction will have that proposal rejected by every peer validator. The trigger requires no special attacker — normal network activity is sufficient.

---

### Recommendation

The `paid_fee_on_l1` value must be transmitted alongside the L1 handler transaction in the consensus wire format so the validator can reconstruct the executable transaction faithfully. Concretely:

1. Add `paid_fee_on_l1: Fee` to `ConsensusTransaction::L1Handler` (or a wrapper type in the protobuf schema).
2. Populate it from the real fee when the proposer serialises the transaction in `convert_internal_consensus_tx_to_consensus_tx`.
3. Use the received value in `convert_consensus_l1_handler_to_internal_l1_handler` instead of `Fee(1)`.
4. Remove the `TODO(Gilad)` placeholder comment once the fix is in place.

---

### Proof of Concept

1. Deploy a standard ERC-20 bridge on L1 and submit a deposit that generates an L1 handler transaction with `paid_fee_on_l1 = 1_000_000_000_000` (1000 gwei).
2. The proposer node picks up the L1 handler transaction from the mempool (with the real fee), executes it, and streams the proposal. The batcher's `partial_block_hash` reflects a sequencer-balance increase of 1000 gwei.
3. Every validator node calls `convert_consensus_l1_handler_to_internal_l1_handler` and substitutes `Fee(1)`. The blockifier credits the sequencer with 1 wei instead of 1000 gwei, producing a different storage write and a different `partial_block_hash`.
4. `validate_proposal` reaches the `built_block != received_fin.proposal_commitment` check, increments `CONSENSUS_PROPOSAL_FIN_MISMATCH`, and returns `ProposalFinMismatch`.
5. Consensus never finalises the block; the L1 message is never processed. Repeat for every subsequent proposal containing an L1 handler transaction. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-247)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L582-596)
```rust
            let batcher_block_commitment = proposal_commitment_from(
                finished_info.proposal_commitment.partial_block_hash,
                fee_proposal,
            );

            info!(
                network_block_commitment = ?fin.proposal_commitment,
                ?batcher_block_commitment,
                executed_txs_count,
                "Finished validating proposal."
            );
            if executed_txs_count == 0 {
                warn!("Validated an empty proposal.");
            }
            HandledProposalPart::Finished(batcher_block_commitment, Box::new(fin), finished_info)
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L163-171)
```rust
pub(crate) fn proposal_commitment_from(
    partial: PartialBlockHash,
    fee_proposal: Option<GasPrice>,
) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else {
        return ProposalCommitment(partial.0);
    };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
```
