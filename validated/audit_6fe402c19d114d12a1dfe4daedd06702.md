### Title
`is_proposal_init_valid` Does Not Validate `ProposalInit.builder`, Allowing Arbitrary Sequencer Address in Block Execution — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_proposal_init_valid` validates many fields of `ProposalInit` (height, L1/L2 gas prices, timestamp, starknet version, fee proposal, version constant commitment, L1 DA mode) but never checks the `builder` field. Because `convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` passed to the batcher, a malicious proposer can inject an arbitrary sequencer address into every block they propose. Validators accept the proposal without objection, the batcher executes all transactions under the spoofed sequencer address, and the resulting `PartialBlockHashComponents` — and therefore the `ProposalCommitment` — are computed with the attacker-chosen address. The `ProposalFin` comparison passes because both sides derive the commitment from the same unvalidated `init.builder`.

### Finding Description

`ProposalInit` carries a `builder` field:

```
/// Address of the one who builds/sequences the block.
pub builder: ContractAddress,
``` [1](#0-0) 

The proposer sets it from a local static config value:

```rust
builder: args.builder_address,
``` [2](#0-1) 

`convert_to_sn_api_block_info` maps it directly to `sequencer_address`:

```rust
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← proposer-supplied, unvalidated
    ...
})
``` [3](#0-2) 

This `BlockInfo` is forwarded to the batcher via `ValidateBlockInput.block_info` in `initiate_validation`:

```rust
block_info: convert_to_sn_api_block_info(init)?,
``` [4](#0-3) 

The batcher then builds `PartialBlockHashComponents` from that `BlockInfo`:

```rust
let partial_block_hash_components =
    PartialBlockHashComponents::new(&block_info, header_commitments);
``` [5](#0-4) 

`PartialBlockHashComponents::new` stores `sequencer_address` as the `sequencer` field:

```rust
sequencer: SequencerContractAddress(block_info.sequencer_address),
``` [6](#0-5) 

`sequencer` is then hashed into the block hash:

```rust
.chain(&partial_block_hash_components.sequencer.0)
``` [7](#0-6) 

Meanwhile, `is_proposal_init_valid` checks height, L1/L2 gas prices, timestamp, starknet version, fee proposal, version constant commitment, and L1 DA mode — but `ProposalInitValidation` has no `builder` field and the function never inspects `init.builder`:

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    pub fee_actual: Option<GasPrice>,
    // builder is absent
}
``` [8](#0-7) 

The final commitment check at line 244 compares the batcher-computed commitment against the proposer-supplied `ProposalFin.proposal_commitment`. Because both sides derive the commitment from the same unvalidated `init.builder`, the check passes regardless of what address the proposer chose:

```rust
if built_block != received_fin.proposal_commitment {
    return Err(ValidateProposalError::ProposalFinMismatch);
}
``` [9](#0-8) 

### Impact Explanation

Three concrete harms follow from an arbitrary `builder`:

1. **Wrong `get_execution_info` syscall result.** Every transaction executed in the block calls the `get_execution_info` syscall, which returns `sequencer_address` from `block_info`. Contracts that branch on `sequencer_address` (e.g., fee-bypass logic, sequencer-only admin gates) receive the attacker-chosen address instead of the legitimate one, producing wrong execution outcomes.

2. **Wrong block hash committed to storage and L1.** `sequencer_address` is a direct input to the Poseidon block hash. A spoofed address produces a different block hash than the honest one, corrupting the chain's authoritative state root chain and breaking L1 verification.

3. **Fee misdirection.** Transaction fees are credited to `sequencer_address`. A malicious proposer can redirect all fees collected in their proposed block to an address they control, draining value from the legitimate sequencer.

### Likelihood Explanation

Any validator that wins a proposal round can exploit this. No special privilege beyond being a consensus participant is required. The attacker simply sets `builder` to an arbitrary address in the `ProposalInit` they broadcast; all honest validators accept the proposal because `is_proposal_init_valid` never inspects the field. The attack is repeatable every round the malicious validator is selected as proposer.

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`:

```rust
pub(crate) struct ProposalInitValidation {
    ...
    pub builder: ContractAddress,  // expected builder address
}

// In is_proposal_init_valid:
if init_proposed.builder != proposal_init_validation.builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!(
            "builder mismatch: expected={:?}, proposed={:?}",
            proposal_init_validation.builder, init_proposed.builder
        ),
    ));
}
```

The validator should populate `ProposalInitValidation.builder` from its own local `builder_address` config (the same source the proposer uses), mirroring the pattern already used for `l2_gas_price_fri`, `l1_da_mode`, and `starknet_version`.

### Proof of Concept

1. Attacker controls a validator node and is selected as proposer for round R at height H.
2. Attacker sets `init.builder = attacker_address` (any address they control) in the `ProposalInit` they broadcast.
3. Honest validators receive the `ProposalInit`, call `is_proposal_init_valid` — no check on `builder` — and proceed to `initiate_validation`.
4. `initiate_validation` calls `convert_to_sn_api_block_info(init)`, producing `BlockInfo { sequencer_address: attacker_address, ... }`.
5. The batcher executes all transactions with `sequencer_address = attacker_address`. Every `get_execution_info` syscall returns `attacker_address` as the sequencer. All fees are credited to `attacker_address`.
6. `PartialBlockHashComponents::new` stores `sequencer: attacker_address`; `calculate_block_hash` hashes it in. The resulting `ProposalCommitment` matches the proposer's `ProposalFin` (both used the same `init.builder`), so the `ProposalFinMismatch` check passes.
7. Consensus reaches decision; the block is committed with the wrong sequencer address, wrong block hash, and misdirected fees. [10](#0-9) [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-107)
```rust
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L174-174)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-246)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L466-466)
```rust
        block_info: convert_to_sn_api_block_info(init)?,
```

**File:** crates/apollo_batcher/src/block_builder.rs (L178-179)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-281)
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
```
