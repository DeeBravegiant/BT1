### Title
Unvalidated `builder` Field in `ProposalInit` Allows Malicious Proposer to Inject Arbitrary `sequencer_address` into Block Context and Block Hash — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit.builder` is the Sequencer's analog to EVM's `COINBASE` opcode: it is the block-builder address that flows directly into `BlockInfo.sequencer_address`, is returned by the `get_sequencer_address()` syscall in execute mode, and is hashed into the final block hash via `PartialBlockHashComponents`. The validator's `is_proposal_init_valid` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `timestamp`, `starknet_version`, `version_constant_commitment`, and L1 gas prices — but never checks `builder`. A malicious proposer can therefore set `builder` to any value (zero, an attacker-controlled address, etc.) and every validator will accept the proposal, execute transactions against the wrong sequencer address, and commit a block whose hash encodes that wrong address.

### Finding Description

`ProposalInit` carries a `builder` field (the block-builder/sequencer address): [1](#0-0) 

During proposal building, the proposer sets it from its local static config: [2](#0-1) 

`convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is handed to the blockifier: [3](#0-2) 

`PartialBlockHashComponents::new` then copies `block_info.sequencer_address` into the component that is hashed into the block hash: [4](#0-3) 

`calculate_block_hash` chains the sequencer address into the Poseidon hash: [5](#0-4) 

The validator's `is_proposal_init_valid` checks several fields but has **no check on `builder`**: [6](#0-5) [7](#0-6) 

`ProposalInitValidation` does not carry a `builder` field at all, so there is no reference value to compare against: [6](#0-5) 

The `proposer` field is validated against the committee in the consensus manager, but `builder` is never validated anywhere in the proposal-validation path: [8](#0-7) 

Additionally, the default value for `builder_address` in the config schema is `"0x0"`: [9](#0-8) 

### Impact Explanation

1. **Wrong `get_sequencer_address()` syscall result.** In execute mode, `allocate_block_info_segment` writes `block_info.sequencer_address` (derived from `init.builder`) into the Cairo VM memory segment that contracts read via `get_execution_info`. A malicious proposer setting `builder = 0` or `builder = attacker_address` causes every contract that calls `get_sequencer_address()` in that block to receive the wrong value. Contracts that use this for fee routing, access control, or any privileged operation will behave incorrectly. [10](#0-9) 

2. **Wrong block hash committed to the chain.** Because `sequencer_address` is a direct input to `calculate_block_hash`, a manipulated `builder` value produces a different block hash than the honest value. All downstream consumers of the block hash (retrospective hash checks, L1 anchoring, ZK proofs) operate on a hash that encodes the attacker-chosen sequencer address.

3. **`ProposalFin` commitment check does not catch this.** Both proposer and validator derive `BlockInfo.sequencer_address` from the same `init.builder`, so they compute identical `PartialBlockHash` values and the `built_block == received_fin.proposal_commitment` check passes regardless of what `builder` contains. [11](#0-10) 

### Likelihood Explanation

The attacker must be selected as a proposer for a given height/round, which requires being a staking committee member. This is a privileged but realistic threat model (a compromised or malicious validator). Once selected, the attack requires only setting one field in the outbound `ProposalInit` message — no cryptographic forgery, no network-level attack.

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`. Each validator node already has `builder_address` in its static config; the validator should reject any `ProposalInit` whose `builder` field does not match the locally-configured expected builder address (or a network-wide agreed value). This mirrors how `proposer` is validated against the committee.

### Proof of Concept

1. A malicious node is selected as proposer for height H.
2. It constructs `ProposalInit { builder: ContractAddress::ZERO, ... }` (all other fields valid).
3. It streams this `ProposalInit` to all validators.
4. Validators call `is_proposal_init_valid`: all checked fields pass; `builder` is never checked.
5. Validators call `initiate_validation` → `convert_to_sn_api_block_info(&init)` → `BlockInfo { sequencer_address: 0, ... }`.
6. All transactions in the block execute with `sequencer_address = 0`. Any contract calling `get_sequencer_address()` receives `0`.
7. `PartialBlockHashComponents::new(&block_info, ...)` sets `sequencer = SequencerContractAddress(0)`.
8. `calculate_block_hash` hashes `0` in the sequencer slot, producing a block hash that differs from what an honest proposer would have produced.
9. Both proposer and validator compute the same (wrong) `ProposalCommitment`; the `ProposalFin` check passes; the block is committed with the wrong sequencer address and wrong block hash. [12](#0-11) [13](#0-12)

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-348)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let proposal_init_info = PreviousProposalInitInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
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
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L224-235)
```rust
    pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
        Self {
            header_commitments,
            block_number: block_info.block_number,
            l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
            l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
            l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-259)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L75-85)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L252-321)
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
    }
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

**File:** crates/apollo_node/resources/config_schema.json (L2797-2801)
```json
  "consensus_manager_config.context_config.static_config.builder_address": {
    "description": "The address of the contract that builds the block.",
    "privacy": "Public",
    "value": "0x0"
  },
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L382-393)
```rust
        let block_info = match self.base.context.execution_mode {
            ExecutionMode::Execute => self.base.context.tx_context.block_context.block_info(),
            ExecutionMode::Validate => {
                &self.base.context.tx_context.block_context.block_info_for_validate()
            }
        };
        let block_data = vec![
            Felt::from(block_info.block_number.0),
            Felt::from(block_info.block_timestamp.0),
            Felt::from(block_info.sequencer_address),
        ];
        let (block_info_segment_start_ptr, _) = self.allocate_data_segment(vm, &block_data)?;
```
