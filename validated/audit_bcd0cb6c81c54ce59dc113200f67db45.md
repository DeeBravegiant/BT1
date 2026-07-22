### Title
Unvalidated `ProposalInit.builder` Field Allows Proposer to Redirect All Block Fees to an Arbitrary Address — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit` carries two identity fields: `proposer` (the consensus identity) and `builder` (the address used as `sequencer_address` / fee recipient during block execution). The `proposer` field is verified against the committee-derived expected proposer in both `manager.rs` and `single_height_consensus.rs`. The `builder` field is **never validated** anywhere in the proposal-validation path. Because `builder` is passed verbatim into `convert_to_sn_api_block_info` as `sequencer_address`, a legitimate proposer can set it to any arbitrary address and redirect all transaction fees for that block to an attacker-controlled account. Validators will accept the proposal and compute a matching block commitment, because they also use the proposer-supplied `builder` value.

### Finding Description

`ProposalInit` is defined with two distinct address fields: [1](#0-0) 

During proposal building, `builder` is set from the local config: [2](#0-1) 

The `proposer` field is checked against the committee-derived expected proposer in the consensus manager: [3](#0-2) 

And again in `SingleHeightConsensus`: [4](#0-3) 

However, `is_proposal_init_valid` — the function that validates all `ProposalInit` fields before the proposal is accepted — checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, all four L1 gas prices, `timestamp`, and `fee_proposal_fri`, but **never checks `builder`**: [5](#0-4) 

The unchecked `builder` field is then passed directly into `convert_to_sn_api_block_info`, where it becomes `sequencer_address` — the fee recipient for every transaction in the block: [6](#0-5) 

This `sequencer_address` is also written into the block header committed to state sync: [7](#0-6) 

Because validators use the proposer-supplied `builder` value to execute the block and compute the block commitment, the proposer's commitment and the validator's commitment will agree — the proposal passes `ProposalFinMismatch` check — and the block is committed with the wrong fee recipient. [8](#0-7) 

### Impact Explanation

Every transaction fee in the block is transferred to `sequencer_address` (i.e., `init.builder`). A malicious proposer sets `builder` to an attacker-controlled address. Validators accept the proposal without objection. The committed block's state diff records fee token balance increases for the attacker's address instead of the legitimate sequencer. This is a direct, irreversible economic loss: all fees from every transaction in the block are stolen. The wrong `sequencer_address` is also embedded in the block header commitment, producing a wrong state root.

**Impact category:** Critical — Incorrect fee/balance effect with economic impact; wrong state committed.

### Likelihood Explanation

The attacker must be the legitimate proposer for a consensus round. In a multi-validator network, each validator takes turns proposing. Any validator who is scheduled to propose can exploit this on their turn, with no additional preconditions. The attack requires only setting one field in the `ProposalInit` struct before broadcasting the proposal.

### Recommendation

Add a check in `is_proposal_init_valid` that verifies `init_proposed.builder` equals the locally-configured `builder_address` (the same value used in `ProposalBuildArguments::builder_address`). The `ProposalInitValidation` struct should carry the expected `builder_address`, and `is_proposal_init_valid` should reject any proposal where `init_proposed.builder != expected_builder_address`, analogous to how `proposer` is checked against the committee-derived value.

### Proof of Concept

1. Attacker is the legitimate proposer for height H, round R (verified by committee).
2. Attacker constructs `ProposalInit` with all valid fields (height, gas prices, etc.) but sets `builder = attacker_address`.
3. Attacker broadcasts the proposal stream.
4. Each validator calls `is_proposal_init_valid` — passes, because `builder` is not checked.
5. Each validator calls `initiate_validation` → `convert_to_sn_api_block_info(&init)` → `sequencer_address = attacker_address`.
6. Batcher executes all transactions with `sequencer_address = attacker_address`; all fee transfers go to `attacker_address`.
7. Batcher computes `partial_block_hash` using `attacker_address` as sequencer; proposer's `ProposalFin.proposal_commitment` was computed the same way — commitments match.
8. Consensus reaches decision; block is committed with `attacker_address` as sequencer and all fees credited to attacker.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L102-107)
```rust
    /// Address of the one who proposed the block in consensus.
    pub proposer: ContractAddress,
    /// Block timestamp.
    pub timestamp: u64,
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L169-174)
```rust
    let init = ProposalInit {
        height: args.build_param.height,
        round: args.build_param.round,
        valid_round: args.build_param.valid_round,
        proposer: args.build_param.proposer,
        builder: args.builder_address,
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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L114-119)
```rust
        let Ok(proposer_id) = self.committee.get_proposer(height, init.round) else {
            return VecDeque::new();
        };
        if init.proposer != proposer_id {
            warn!("Invalid proposer: expected {:?}, got {:?}", proposer_id, init.proposer);
            return VecDeque::new();
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L243-247)
```rust
    // TODO(matan): Switch to signature validation.
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L252-320)
```rust
#[instrument(level = "warn", skip_all, fields(?proposal_init_validation, ?init_proposed))]
async fn is_proposal_init_valid(
    proposal_init_validation: &ProposalInitValidation,
    init_proposed: &ProposalInit,
    clock: &dyn Clock,
    l1_gas_price_provider: Arc<dyn L1GasPriceProviderClient>,
    gas_price_params: &GasPriceParams,
) -> ValidateProposalResult<()> {
    let now: u64 = clock.unix_now();
    let last_block_timestamp =
        proposal_init_validation.previous_proposal_init.as_ref().map_or(0, |info| info.timestamp);
    if init_proposed.timestamp < last_block_timestamp {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is too old: last_block_timestamp={}, proposed={}",
                last_block_timestamp, init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.timestamp > now + proposal_init_validation.block_timestamp_window_seconds {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is in the future: now={}, block_timestamp_window_seconds={}, \
                 proposed={}",
                now,
                proposal_init_validation.block_timestamp_window_seconds,
                init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.starknet_version != proposal_init_validation.starknet_version {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "starknet_version mismatch: expected={:?}, proposed={:?}",
                proposal_init_validation.starknet_version, init_proposed.starknet_version
            ),
        ));
    }
    // `version_constant_commitment` is proposer-supplied (network-derived). It is not yet a real
    // commitment (see `expected_version_constant_commitment`): the only valid value is the
    // sentinel, so reject anything else. Enforcing the same value the proposer emits keeps the two
    // sides in lockstep, so a real value cannot ship on one side without the other.
    let expected_commitment = expected_version_constant_commitment();
    if init_proposed.version_constant_commitment != expected_commitment {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "version_constant_commitment mismatch: expected={expected_commitment:?}, \
                 proposed={:?}",
                init_proposed.version_constant_commitment
            ),
        ));
    }
    if !(init_proposed.height == proposal_init_validation.height
        && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
        && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri)
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            "ProposalInit validation failed".to_string(),
        ));
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L329-347)
```rust
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
            strk_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_fri,
                l1_data_gas_price: l1_data_gas_price_fri,
                l2_gas_price: l2_gas_price_fri,
            },
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
        },
        use_kzg_da: init.l1_da_mode.is_use_kzg_da(),
        starknet_version: init.starknet_version,
    })
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L397-412)
```rust
        let sequencer = SequencerContractAddress(init.builder);

        let block_header_without_hash = BlockHeaderWithoutHash {
            block_number: height,
            l1_gas_price,
            l1_data_gas_price,
            l2_gas_price,
            l2_gas_consumed: l2_gas_used,
            next_l2_gas_price: self.l2_gas_price,
            sequencer,
            timestamp: BlockTimestamp(init.timestamp),
            l1_da_mode: init.l1_da_mode,
            fee_proposal_fri: init.fee_proposal_fri,
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };
```
