Let me look at the `builder` field usage in block hash computation and fee collection to confirm the impact.

### Title
Missing `builder` Validation in `is_proposal_init_valid` Allows Proposer to Inject Arbitrary Sequencer Address into Committed Block - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit.builder` is accepted verbatim from the network and passed directly into `BlockInfo.sequencer_address` without any validation in `is_proposal_init_valid`. A malicious proposer can set `builder` to any address. The validator executes the block with that address as the sequencer, the block hash is computed over it, and the `ProposalFinMismatch` guard does not fire because both sides derive the hash from the same attacker-supplied value. The committed block carries the wrong sequencer address, redirecting all fee payments and corrupting every `get_sequencer_address` syscall result for that block.

---

### Finding Description

`ProposalInit` carries a `builder` field (the block-builder/sequencer address). [1](#0-0) 

`convert_to_sn_api_block_info` maps `init.builder` directly to `BlockInfo.sequencer_address` with no guard: [2](#0-1) 

This `BlockInfo` is passed to the batcher for both the proposer path (`initiate_build` → `propose_block`) and the validator path (`initiate_validation` → `validate_block`): [3](#0-2) 

`is_proposal_init_valid` validates `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, `starknet_version`, `version_constant_commitment`, `timestamp`, and `fee_proposal_fri`. It does **not** validate `builder`: [4](#0-3) 

`ProposalInitValidation` — the struct that carries the validator's reference values — has no `builder` field at all: [5](#0-4) 

The only downstream guard is the `ProposalFinMismatch` check: [6](#0-5) 

This check compares the validator's locally computed `partial_block_hash` against the proposer's `ProposalFin.proposal_commitment`. Because the validator derives its hash from the same attacker-supplied `builder` value (via `PartialBlockHashComponents::new` → `sequencer` field), both sides produce identical hashes. The mismatch guard is blind to the manipulation. [7](#0-6) 

---

### Impact Explanation

**Wrong syscall result (Critical).** The `get_sequencer_address` syscall reads `block_info.sequencer_address` directly: [8](#0-7) 

Every contract that calls `get_sequencer_address` during that block receives the attacker-controlled address instead of the legitimate sequencer address. This corrupts execution results and storage writes that depend on the sequencer identity.

**Wrong fee destination (Critical).** Fee collection uses `block_context.block_info.sequencer_address` to locate the sequencer's balance slot: [9](#0-8) 

With `builder` set to an attacker-controlled address, all transaction fees for the block are credited to that address. The legitimate sequencer receives nothing; the attacker captures the full block's fee revenue.

**Wrong block hash committed (Critical).** `sequencer_address` is a direct input to `calculate_block_hash` via `PartialBlockHashComponents`: [10](#0-9) 

The committed block hash is therefore computed over the wrong sequencer address, producing a permanently incorrect on-chain state root chain.

---

### Likelihood Explanation

Any validator that becomes the Tendermint proposer for a round can trigger this. Proposer selection is round-robin over the validator set — no special privilege beyond being a validator is required. The attack requires only crafting a `ProposalInit` with a chosen `builder` value, which is a normal part of the proposal message. No out-of-band access, no configuration change, and no cooperation from other nodes is needed.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`:

```rust
// In ProposalInitValidation:
pub expected_builder: ContractAddress,

// In is_proposal_init_valid:
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!(
            "builder mismatch: expected={:?}, proposed={:?}",
            proposal_init_validation.expected_builder,
            init_proposed.builder
        ),
    ));
}
```

Populate `expected_builder` from the node's own configured `builder_address` when constructing `ProposalInitValidation` in `set_height_and_round`. [11](#0-10) 

---

### Proof of Concept

1. A validator node is selected as the Tendermint proposer for height H, round R.
2. In `initiate_build`, the proposer constructs `ProposalInit` with `builder = ATTACKER_ADDRESS` instead of its own `builder_address`. [12](#0-11) 
3. The proposer streams `ProposalPart::Init(init)` to all validators.
4. Each validator calls `validate_proposal` → `is_proposal_init_valid`. None of the checks cover `builder`; the function returns `Ok(())`. [13](#0-12) 
5. `initiate_validation` calls `convert_to_sn_api_block_info(init)`, producing `BlockInfo { sequencer_address: ATTACKER_ADDRESS, … }`. [2](#0-1) 
6. The batcher executes all transactions with `sequencer_address = ATTACKER_ADDRESS`. Fees accumulate to `ATTACKER_ADDRESS`; `get_sequencer_address` returns `ATTACKER_ADDRESS`.
7. `BlockExecutionArtifacts::new` computes `PartialBlockHashComponents` with `sequencer = ATTACKER_ADDRESS`. [14](#0-13) 
8. The validator's `batcher_block_commitment` equals the proposer's `fin.proposal_commitment` (both derived from `ATTACKER_ADDRESS`). `ProposalFinMismatch` does not fire. [15](#0-14) 
9. Consensus reaches decision; the block is committed with `sequencer_address = ATTACKER_ADDRESS` and the wrong block hash.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L107-107)
```rust
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L329-332)
```rust
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L153-160)
```rust
    is_proposal_init_valid(
        &args.proposal_init_validation,
        &args.init,
        args.deps.clock.as_ref(),
        args.deps.l1_gas_price_provider,
        &args.gas_price_params,
    )
    .await?;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L243-247)
```rust
    // TODO(matan): Switch to signature validation.
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L443-476)
```rust
async fn initiate_validation(
    batcher: Arc<dyn BatcherClient>,
    state_sync_client: SharedStateSyncClient,
    init: &ProposalInit,
    proposal_id: ProposalId,
    timeout_plus_margin: Duration,
    clock: &dyn Clock,
    compare_retrospective_block_hash: bool,
) -> ValidateProposalResult<()> {
    let chrono_timeout = chrono::Duration::from_std(timeout_plus_margin)
        .expect("Can't convert timeout to chrono::Duration");

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
    Ok(())
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-235)
```rust
impl PartialBlockHashComponents {
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L256-258)
```rust
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L725-733)
```rust
    fn get_sequencer_address(
        _request: GetSequencerAddressRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<GetSequencerAddressResponse> {
        syscall_handler.verify_not_in_validate_mode("get_sequencer_address")?;
        Ok(GetSequencerAddressResponse {
            address: syscall_handler.get_block_info().sequencer_address,
        })
```

**File:** crates/blockifier/src/fee/fee_utils.rs (L205-208)
```rust
pub fn get_sequencer_balance_keys(block_context: &BlockContext) -> (StorageKey, StorageKey) {
    let sequencer_address = block_context.block_info.sequencer_address;
    get_address_balance_keys(sequencer_address)
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1178-1198)
```rust
        let proposal_init_validation = ProposalInitValidation {
            height: init.height,
            block_timestamp_window_seconds: self
                .config
                .static_config
                .block_timestamp_window_seconds,
            previous_proposal_init: self.previous_proposal_init.clone(),
            l1_da_mode: self.l1_da_mode,
            l2_gas_price_fri: self
                .config
                .dynamic_config
                .override_l2_gas_price_fri
                .map(GasPrice)
                .unwrap_or(self.l2_gas_price),
            starknet_version: StarknetVersion::LATEST,
            fee_actual: compute_fee_actual(
                &self.fee_proposals_window,
                init.height,
                VersionedConstants::latest_constants().fee_proposal_window_size,
            ),
        };
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L169-188)
```rust
    let init = ProposalInit {
        height: args.build_param.height,
        round: args.build_param.round,
        valid_round: args.build_param.valid_round,
        proposer: args.build_param.proposer,
        builder: args.builder_address,
        timestamp,
        l1_da_mode: args.l1_da_mode,
        l2_gas_price_fri: args.l2_gas_price,
        l1_gas_price_wei: l1_prices_wei.l1_gas_price,
        l1_data_gas_price_wei: l1_prices_wei.l1_data_gas_price,
        l1_gas_price_fri: l1_prices_fri.l1_gas_price,
        l1_data_gas_price_fri: l1_prices_fri.l1_data_gas_price,
        starknet_version: starknet_api::block::StarknetVersion::LATEST,
        // TODO(Asmaa): Put the real value once we have it.
        // Sentinel until then; see `expected_version_constant_commitment` for why this is the
        // single source of truth shared with the validator.
        version_constant_commitment: expected_version_constant_commitment(),
        fee_proposal_fri: Some(args.fee_proposal),
    };
```

**File:** crates/apollo_batcher/src/block_builder.rs (L178-179)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```
