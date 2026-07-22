### Title
`valid_proposals` Populated with Mismatched Proposal Before Commitment Verification — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

In `validate_proposal()`, the validator inserts the fully-executed proposal content into the shared `valid_proposals` map **before** checking whether the batcher-computed `ProposalCommitment` matches the proposer-supplied `ProposalFin.proposal_commitment`. When the two commitments differ, the function returns `Err(ProposalFinMismatch)` — but the poisoned entry is already permanently stored in `valid_proposals` and is never removed. Any subsequent `repropose()` call for the same `(height, round)` will read and re-broadcast the mismatched content as if it were a valid, agreed-upon block.

---

### Finding Description

In `validate_proposal()`, the sequence is:

```
// Step 1 – insert into shared map (BEFORE the commitment check)
valid_proposals.insert_proposal(args.init, content, &args.proposal_id, finished_info);

// Step 2 – check commitment (AFTER the insert)
if built_block != received_fin.proposal_commitment {
    return Err(ValidateProposalError::ProposalFinMismatch);
}
``` [1](#0-0) 

`insert_proposal` writes the entry into `BuiltProposals::data` keyed by `(height, round)`: [2](#0-1) 

There is no rollback path. When `ProposalFinMismatch` is returned, the entry remains in `valid_proposals`. The comment at line 238–239 explicitly acknowledges the ordering is intentional to avoid a race with `repropose`, but it does not account for the case where the subsequent commitment check fails.

`repropose()` later calls `update_for_reproposal`, which reads from `valid_proposals` by `(height, round)` and re-streams the stored content verbatim: [3](#0-2) 

The `insert_proposal` assert at line 199–203 enforces "at most one proposal per (height, round)", so a legitimate proposal for the same round cannot overwrite the poisoned entry: [4](#0-3) 

---

### Impact Explanation

A malicious proposer can craft a `ProposalFin` whose `proposal_commitment` deliberately mismatches the batcher's computed commitment. The validator:

1. Executes all transactions and stores the full block content in `valid_proposals`.
2. Detects the mismatch and returns `Err(ProposalFinMismatch)` — consensus votes `None` for this round.
3. Consensus advances to a new round and calls `repropose()` with the same `(height, valid_round)`.
4. `repropose()` reads the poisoned entry and re-broadcasts the content with the **batcher-computed** commitment (not the proposer's mismatched one), but the stored `FinishedProposalInfo` (including `block_header_commitments`, `l2_gas_used`, `final_n_executed_txs`) came from a proposal that was explicitly rejected.

The corrupted `FinishedProposalInfo` flows into `send_reproposal` → `ProposalFin` → `CommitmentParts` → `decision_reached` → `commit_proposal` in the batcher, potentially causing the wrong state diff, wrong receipt commitment, wrong event commitment, or wrong L2 gas accounting to be committed to the chain. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires a proposer that sends a valid transaction stream but a deliberately wrong `ProposalFin.proposal_commitment`. This is a network-reachable condition: any node acting as proposer can craft such a message. The `ProposalFinMismatch` path is exercised in the existing test suite (`proposal_fin_mismatch` test), confirming the code path is reachable. The race condition the comment describes (repropose before insert) is real, so the ordering cannot simply be reversed without a different fix. [6](#0-5) 

---

### Recommendation

Move `insert_proposal` to **after** the commitment check succeeds, and address the repropose race condition separately — for example by holding the `valid_proposals` lock across both the insert and the `fin_sender.send()` call (which is already done in `validate_and_send`), or by removing the stale entry on `ProposalFinMismatch`:

```rust
// Correct ordering:
if built_block != received_fin.proposal_commitment {
    CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
    return Err(ValidateProposalError::ProposalFinMismatch);
}

let mut valid_proposals = args.valid_proposals.lock().unwrap();
valid_proposals.insert_proposal(args.init, content, &args.proposal_id, finished_info);
```

Alternatively, add a `remove_proposal(height, round)` call in the `ProposalFinMismatch` branch so the poisoned entry is cleaned up before returning the error.

---

### Proof of Concept

1. Attacker controls a proposer node for height H, round 0.
2. Proposer streams a valid `ProposalInit` + N transaction batches to the validator.
3. Validator calls `initiate_validation` → batcher executes all N transactions and computes `commitment_A`.
4. Proposer sends `ProposalFin { proposal_commitment: commitment_B, executed_transaction_count: N }` where `commitment_B ≠ commitment_A`.
5. In `validate_proposal`:
   - `handle_proposal_part` returns `HandledProposalPart::Finished(commitment_A, fin_with_B, finished_info)`.
   - Line 241: `valid_proposals.insert_proposal(init, content, proposal_id, finished_info)` — entry stored at `(H, round=0)`.
   - Line 244: `commitment_A != commitment_B` → `return Err(ProposalFinMismatch)`.
6. Consensus votes `None`; round advances to 1. Consensus calls `repropose(commitment_A, BuildParam { round: 1, valid_round: Some(0) })`.
7. `update_for_reproposal` reads the entry at `(H, round=0)` — finds the poisoned `finished_info` — inserts a new entry at `(H, round=1)` and returns it.
8. `send_reproposal` re-streams the content with `CommitmentParts::from(&finished_info)` carrying the batcher's state diff, receipt commitment, and event commitment from the rejected proposal.
9. If quorum is reached on round 1, `decision_reached` commits this block with potentially wrong commitments. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L238-249)
```rust
    // Update valid_proposals before sending fin to avoid a race condition
    // with `repropose` being called before `valid_proposals` is updated.
    let mut valid_proposals = args.valid_proposals.lock().unwrap();
    valid_proposals.insert_proposal(args.init, content, &args.proposal_id, finished_info);

    // TODO(matan): Switch to signature validation.
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }

    Ok(built_block)
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L180-204)
```rust
    pub(crate) fn insert_proposal(
        &mut self,
        init: ProposalInit,
        transactions: Vec<Vec<InternalConsensusTransaction>>,
        proposal_id: &ProposalId,
        finished_info: FinishedProposalInfo,
    ) {
        let proposal_commitment = proposal_commitment_from(
            finished_info.proposal_commitment.partial_block_hash,
            init.fee_proposal_fri,
        );

        let height = init.height;
        let round = init.round;
        let by_round = self.data.entry(height).or_default();
        let previous = by_round.insert(
            round,
            (proposal_commitment, (init, transactions, *proposal_id, finished_info)),
        );
        assert!(
            previous.is_none(),
            "Overwriting existing proposal for height {height} round {round}; at most one \
             proposal per (height, round) is allowed"
        );
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L211-230)
```rust
    fn update_for_reproposal(
        &mut self,
        height: &BlockNumber,
        proposal_commitment: &ProposalCommitment,
        build_param: &BuildParam,
    ) -> (ProposalInit, Vec<Vec<InternalConsensusTransaction>>, FinishedProposalInfo) {
        let lookup_round = build_param.valid_round.expect("Valid round must be set for reproposal");
        let (mut init, transactions, proposal_id, finished_info) =
            self.get_proposal(height, &lookup_round, proposal_commitment).clone();
        init.round = build_param.round;
        init.proposer = build_param.proposer;
        init.valid_round = build_param.valid_round;
        self.insert_proposal(
            init.clone(),
            transactions.clone(),
            &proposal_id,
            finished_info.clone(),
        );
        (init, transactions, finished_info)
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1292-1301)
```rust
async fn validate_and_send(
    args: ProposalValidateArguments,
    fin_sender: oneshot::Sender<ProposalCommitment>,
) -> Result<ProposalCommitment, ValidateProposalError> {
    let proposal_commitment = validate_proposal(args).await?;
    fin_sender
        .send(proposal_commitment)
        .map_err(|_| ValidateProposalError::SendError(proposal_commitment))?;
    Ok(proposal_commitment)
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1303-1343)
```rust
async fn send_reproposal(
    proposal_commitment: ProposalCommitment,
    init: ProposalInit,
    txs: Vec<Vec<InternalConsensusTransaction>>,
    finished_info: FinishedProposalInfo,
    next_l2_gas_price: GasPrice,
    stream_sender: &mut StreamSender,
    transaction_converter: Arc<dyn TransactionConverterTrait>,
) -> Result<(), ReproposeError> {
    stream_sender.send(ProposalPart::Init(init)).await?;
    for batch in txs.into_iter() {
        let transactions = futures::future::join_all(batch.into_iter().map(|tx| {
            // transaction_converter is an external dependency (class manager) and so
            // we can't assume success on reproposal.
            transaction_converter.convert_internal_consensus_tx_to_consensus_tx(tx)
        }))
        .await
        .into_iter()
        .collect::<Result<Vec<_>, _>>()?;
        stream_sender.send(ProposalPart::Transactions(TransactionBatch { transactions })).await?;
    }
    let executed_transaction_count: u64 = finished_info
        .final_n_executed_txs
        .try_into()
        .expect("Number of executed transactions should fit in u64");
    let fin_payload = ProposalFinPayload {
        commitment_parts: CommitmentParts::from(&finished_info),
        l2_gas_info: L2GasInfo {
            next_l2_gas_price_fri: next_l2_gas_price,
            l2_gas_used: finished_info.l2_gas_used,
        },
    };
    let fin = ProposalFin {
        proposal_commitment,
        executed_transaction_count,
        fin_payload: Some(fin_payload),
    };
    stream_sender.send(ProposalPart::Fin(fin)).await?;

    Ok(())
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal_test.rs (L466-504)
```rust
#[tokio::test]
async fn proposal_fin_mismatch() {
    let (mut proposal_args, mut content_sender) = create_proposal_validate_arguments();
    let n_executed = 0;
    // Setup batcher to validate the block.
    proposal_args.deps.batcher.expect_validate_block().returning(|_| Ok(()));
    // Batcher returns a different block hash than the one received in Fin.
    let built_block = PartialBlockHash(Felt::ONE);
    proposal_args
        .deps
        .batcher
        .expect_finish_proposal()
        .withf(move |input: &FinishProposalInput| {
            input.proposal_id == proposal_args.proposal_id
                && input.final_n_executed_txs == n_executed
        })
        .returning(move |_| {
            Ok(FinishProposalStatus::Finished(FinishedProposalInfo {
                artifact: FinishedProposalInfoWithoutParent {
                    proposal_commitment: ProposalCommitment { partial_block_hash: built_block },
                    final_n_executed_txs: n_executed,
                    block_header_commitments: BlockHeaderCommitments::default(),
                    l2_gas_used: GasAmount::default(),
                },
                parent_proposal_commitment: None,
            }))
        });
    let received_fin = ConsensusProposalCommitment::default();
    content_sender
        .send(ProposalPart::Fin(ProposalFin {
            proposal_commitment: received_fin,
            executed_transaction_count: n_executed.try_into().unwrap(),
            fin_payload: None,
        }))
        .await
        .unwrap();

    let res = validate_proposal(proposal_args.into()).await;
    assert!(matches!(res, Err(ValidateProposalError::ProposalFinMismatch)));
```
