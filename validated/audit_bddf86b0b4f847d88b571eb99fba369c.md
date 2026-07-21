### Title
Unvalidated `ProposalInit.builder` Field Allows Elected Proposer to Redirect Block Fees to Arbitrary Address - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit` carries a `builder` field (the sequencer/builder address) that is set by the proposer and transmitted over the network. `is_proposal_init_valid` carefully validates `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, all four L1 gas prices, and `fee_proposal_fri`, but never checks `builder`. Because `builder` flows directly into `convert_to_sn_api_block_info(init)` and becomes the sequencer address for block execution, a malicious elected proposer can set it to any address and redirect all transaction fees from that block to an attacker-controlled wallet. Validators accept any `builder` value because `ProposalInitValidation` contains no expected `builder` field to compare against.

### Finding Description

`ProposalInit.builder` is described as "Address of the one who builds/sequences the block." [1](#0-0) 

The proposer sets it in `initiate_build` from its own local configuration: [2](#0-1) 

The field is transmitted in the `ProposalInit` protobuf message over the P2P network: [3](#0-2) 

When a validator receives a proposal, `validate_proposal` calls `is_proposal_init_valid`. That function validates many fields but the `builder` field is absent from every check: [4](#0-3) 

`ProposalInitValidation` — the struct that carries all locally-derived reference values — has no `builder` field at all, so there is no expected value to compare against: [5](#0-4) 

After `is_proposal_init_valid` passes, `initiate_validation` converts the full `ProposalInit` (including the unvalidated `builder`) into `ValidateBlockInput.block_info` and sends it to the batcher: [6](#0-5) 

Because both the proposer's batcher and the validator's batcher receive the same `builder` value from `ProposalInit`, they execute with the same sequencer address and produce identical `partial_block_hash` values. The `ProposalFinMismatch` check therefore passes: [7](#0-6) 

The block is committed with the attacker-supplied sequencer address, and all transaction fees from that block are credited to the attacker's address.

The consensus-layer check in `handle_proposal` only validates `init.proposer` against the committee-derived expected proposer; it never touches `builder`: [8](#0-7) 

### Impact Explanation

**Critical — Incorrect fee accounting with economic impact.** All transaction fees from the affected block are redirected to an attacker-controlled address instead of the legitimate sequencer/builder address. The corrupted value is `sequencer_address` in the committed block header, which is set to `attacker_wallet_address` instead of the node's legitimate `builder_address`. This is a direct, quantifiable economic loss for the legitimate sequencer operator.

### Likelihood Explanation

Any validator that is elected as proposer for a round can exploit this. In a BFT consensus system, proposer selection rotates among all validators; no special administrative privilege is required beyond being the elected proposer for a single round. The attack requires only constructing a `ProposalInit` with a modified `builder` field, which is trivial.

### Recommendation

Add `builder` to `ProposalInitValidation` and validate it in `is_proposal_init_valid`. The expected `builder` address should be derived from the node's own local configuration (the same `builder_address` used in `initiate_build`), not from the proposer-supplied `ProposalInit`. Concretely:

1. Add `expected_builder: ContractAddress` to `ProposalInitValidation`.
2. Populate it from `self.builder_address` when constructing `ProposalInitValidation` in `validate_proposal` (in `sequencer_consensus_context.rs`).
3. Add a check in `is_proposal_init_valid`:
   ```rust
   if init_proposed.builder != proposal_init_validation.expected_builder {
       return Err(ValidateProposalError::InvalidProposalInit(...));
   }
   ```

### Proof of Concept

1. Attacker is elected as proposer for height H, round R.
2. Attacker constructs `ProposalInit` with `builder = attacker_wallet_address` (any arbitrary address).
3. Attacker's batcher executes the block with `sequencer_address = attacker_wallet_address`; all fees are credited there.
4. Attacker streams `ProposalInit` (with malicious `builder`) and transaction batches to validators.
5. Validators call `is_proposal_init_valid` — `builder` is not checked; validation passes.
6. Validators pass `builder = attacker_wallet_address` to their batcher via `convert_to_sn_api_block_info`.
7. Validators' batchers execute with `sequencer_address = attacker_wallet_address`, producing the same `partial_block_hash` as the proposer.
8. `ProposalFin.proposal_commitment` matches the validator-computed commitment → `ProposalFinMismatch` is not triggered.
9. Block H is committed with `sequencer_address = attacker_wallet_address`.
10. All transaction fees from block H are permanently redirected to the attacker's wallet.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-107)
```rust
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L173-174)
```rust
        proposer: args.build_param.proposer,
        builder: args.builder_address,
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L53-54)
```text
    Address builder                   = 6;
    L1DataAvailabilityMode l1_da_mode = 7;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L74-85)
```rust
#[derive(Clone, Debug)]
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    /// fee_actual from the sliding window. `None` until the window has accumulated
    /// `fee_proposal_window_size` entries (startup / near-genesis).
    pub fee_actual: Option<GasPrice>,
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-247)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L312-321)
```rust
    if !(init_proposed.height == proposal_init_validation.height
        && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
        && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri)
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            "ProposalInit validation failed".to_string(),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-474)
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
    debug!("Initiating validate proposal: input={input:?}");
    batcher.validate_block(input.clone()).await.map_err(|err| {
        ValidateProposalError::Batcher(
            format!("Failed to initiate validate proposal {input:?}."),
            err,
        )
    })?;
```

**File:** crates/apollo_consensus/src/manager.rs (L849-866)
```rust
                let Ok(proposer) =
                    get_proposer_for_height(&self.committee_provider, init.height, init.round)
                        .await
                else {
                    warn!(
                        "VIRTUAL_PROPOSER_LOOKUP_FAILED: Failed to determine virtual proposer for \
                         height {} round {}. Dropping proposal.",
                        init.height.0, init.round
                    );
                    return Ok(VecDeque::new());
                };
                if proposer != init.proposer {
                    warn!(
                        "Invalid proposer for height {} and round {}: expected {:?}, got {:?}",
                        init.height.0, init.round, proposer, init.proposer
                    );
                    return Ok(VecDeque::new());
                }
```
