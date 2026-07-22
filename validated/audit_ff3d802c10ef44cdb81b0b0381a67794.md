### Title
Unvalidated `builder` Field in `ProposalInit` Allows Proposer to Redirect Sequencer Fees and Corrupt Block State - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit.builder` is accepted verbatim from the network and mapped directly to `sequencer_address` in `BlockInfo` without any validation in `is_proposal_init_valid`. Because `sequencer_address` is both the fee-collection address for every transaction in the block and a direct input to `PartialBlockHashComponents` (and therefore the `PartialBlockHash` / `ProposalCommitment`), any legitimate proposer can set `builder` to an arbitrary address, redirect all block fees to that address, and have every validator accept and commit the block with the wrong sequencer address.

### Finding Description

`ProposalInit` carries two identity fields:

- `proposer` – the consensus identity of the block proposer. This **is** checked in `single_height_consensus.rs` against the committee-derived expected proposer.
- `builder` – described as "Address of the one who builds/sequences the block." This is **never** checked anywhere in the validation path. [1](#0-0) 

`is_proposal_init_valid` validates: timestamp, `starknet_version`, `version_constant_commitment`, `height`, `l1_da_mode`, `l2_gas_price_fri`, L1 gas prices, and `fee_proposal_fri`. The `builder` field is absent from both the validation function and the `ProposalInitValidation` struct. [2](#0-1) [3](#0-2) 

`convert_to_sn_api_block_info` then maps `init.builder` directly to `sequencer_address` in the `BlockInfo` passed to the batcher: [4](#0-3) 

`PartialBlockHashComponents::new` then copies `block_info.sequencer_address` into the `sequencer` field: [5](#0-4) 

And `calculate_block_hash` chains `sequencer` into the Poseidon hash that produces the final block hash: [6](#0-5) 

When building, the proposer sets `builder` from its own local config: [7](#0-6) 

The TODO comment there even acknowledges this should eventually come from the committee. Until then, the proposer can freely supply any address.

### Impact Explanation

1. **Fee theft (Critical economic impact):** `sequencer_address` is the fee-collection address for every transaction executed in the block. A malicious proposer sets `builder` to an attacker-controlled address; all transaction fees for that block are credited there instead of the legitimate sequencer.

2. **Wrong committed block state (Critical state corruption):** The `sequencer` field is part of `PartialBlockHashComponents` and therefore part of the `PartialBlockHash` / `ProposalCommitment`. Because validators re-execute the block using the proposer-supplied `builder` address, both proposer and validators compute the same `ProposalCommitment` — the `ProposalFinMismatch` check passes — and the block is committed to storage with the wrong `sequencer_address` in its header. This corrupts the authoritative on-chain state and the block hash chain.

### Likelihood Explanation

Any validator that is legitimately selected as proposer for a round can trigger this. No special privilege beyond normal proposer selection is required. In a live network with multiple validators, this is reachable by any participant in the committee.

### Recommendation

Add a `builder` field to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`. The expected value should be the proposer's committee-registered builder address (as the existing TODO at `sequencer_consensus_context.rs:806` anticipates). Until the committee provides it, the validator should at minimum reject any `builder` that differs from the locally configured `builder_address` for the known proposer.

```rust
// In ProposalInitValidation:
pub expected_builder: ContractAddress,

// In is_proposal_init_valid:
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!("builder mismatch: expected={:?}, proposed={:?}",
            proposal_init_validation.expected_builder, init_proposed.builder),
    ));
}
```

### Proof of Concept

1. Validator A is selected as proposer for height H, round 0.
2. A constructs `ProposalInit` with `builder = ATTACKER_ADDRESS` (any address it controls or chooses).
3. A calls `initiate_build` → `convert_to_sn_api_block_info` maps `builder` → `sequencer_address` → batcher executes the block with `ATTACKER_ADDRESS` as fee recipient.
4. A streams the proposal to peers. Each peer calls `validate_proposal` → `is_proposal_init_valid` — no check on `builder` — → `initiate_validation` → `convert_to_sn_api_block_info` again maps `builder` → `sequencer_address` → batcher re-executes with `ATTACKER_ADDRESS`.
5. Both proposer and validators compute `PartialBlockHashComponents { sequencer: ATTACKER_ADDRESS, ... }` → same `PartialBlockHash` → `ProposalFinMismatch` check passes.
6. Consensus reaches decision; `commit_proposal_and_block` stores the block with `sequencer_address = ATTACKER_ADDRESS`. All transaction fees for height H are credited to `ATTACKER_ADDRESS`. The block hash chain now permanently encodes the wrong sequencer address. [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L141-160)
```rust
pub(crate) async fn validate_proposal(
    mut args: ProposalValidateArguments,
) -> ValidateProposalResult<ProposalCommitment> {
    let mut content = Vec::new();
    let mut verify_and_store_proof_tasks: Vec<VerifyAndStoreProofTask> = Vec::new();
    let now = args.deps.clock.now();

    let Some(deadline) = now.checked_add_signed(chrono::TimeDelta::from_std(args.timeout).unwrap())
    else {
        return Err(ValidateProposalError::CannotCalculateDeadline { timeout: args.timeout, now });
    };

    is_proposal_init_valid(
        &args.proposal_init_validation,
        &args.init,
        args.deps.clock.as_ref(),
        args.deps.l1_gas_price_provider,
        &args.gas_price_params,
    )
    .await?;
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L443-475)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-235)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
/// All information required to calculate a block hash except for the state root and the parent
/// block hash.
pub struct PartialBlockHashComponents {
    pub header_commitments: BlockHeaderCommitments,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub starknet_version: StarknetVersion,
}

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L806-808)
```rust
            // TODO(Asmaa): Get it from committee once we have it.
            builder_address: self.config.static_config.builder_address,
            cancel_token,
```
