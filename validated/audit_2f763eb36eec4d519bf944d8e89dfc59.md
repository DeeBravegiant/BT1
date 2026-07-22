### Title
Unvalidated `ProposalInit.builder` field allows any proposer to inject an arbitrary sequencer address into block state and block hash — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit` carries two identity fields: `proposer` (the consensus identity) and `builder` (the address credited as sequencer/block-builder). The `proposer` field is verified against the committee's expected proposer at two points in the consensus pipeline. The `builder` field is **never validated** anywhere in `is_proposal_init_valid` or anywhere else in the validation path. `builder` flows directly into `BlockInfo.sequencer_address`, which is (a) used by the blockifier to credit transaction fees, (b) hashed into `PartialBlockHashComponents.sequencer` and therefore into the final block hash, and (c) stored in the committed block header. Any node that wins a proposal slot can set `builder` to an arbitrary address, causing the committed block to record the wrong sequencer, misdirect all transaction fees, and produce a wrong block hash.

### Finding Description

`ProposalInit` is defined with a distinct `builder` field:

```
/// Address of the one who proposed the block in consensus.
pub proposer: ContractAddress,
/// Address of the one who builds/sequences the block.
pub builder: ContractAddress,
```

The `proposer` field is checked in two places before the proposal is processed:

- `manager.rs` `handle_proposal`: compares `init.proposer` against `get_proposer_for_height(...)` and drops the proposal on mismatch.
- `single_height_consensus.rs` `handle_proposal`: compares `init.proposer` against `committee.get_proposer(height, init.round)`.

`is_proposal_init_valid` in `validate_proposal.rs` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, `starknet_version`, `version_constant_commitment`, and `fee_proposal_fri`. It does **not** check `builder`. `ProposalInitValidation` does not even carry a `builder` field.

`builder` is consumed without any guard in `convert_to_sn_api_block_info`:

```rust
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← proposer-supplied, never validated
    ...
})
```

This `BlockInfo` is passed to the batcher via `ValidateBlockInput.block_info` (validation path) and `ProposeBlockInput.block_info` (build path). The batcher uses `block_info.sequencer_address` as the fee-collection address for every transaction in the block. It also passes it to `PartialBlockHashComponents::new`:

```rust
sequencer: SequencerContractAddress(block_info.sequencer_address),
```

`PartialBlockHashComponents.sequencer` is chained directly into `calculate_block_hash`:

```rust
.chain(&partial_block_hash_components.sequencer.0)
```

### Impact Explanation

A proposer that wins a round can set `ProposalInit.builder` to any `ContractAddress`. Validators accept the proposal because `is_proposal_init_valid` never checks `builder`. The batcher executes the block with the attacker-chosen `sequencer_address`, so:

1. **Wrong fee/balance**: All transaction fees for the block are credited to the attacker-chosen address instead of the legitimate sequencer. This is a direct economic impact matching the "Incorrect fee … balance … effect" impact class.
2. **Wrong block hash / state commitment**: `PartialBlockHashComponents.sequencer` is set to the injected address, so the `PartialBlockHash` (and therefore `ProposalCommitment`) is computed over the wrong sequencer. The committed block header and the on-chain block hash both reflect the injected address.
3. **Wrong receipt/event context**: Any contract that reads `get_sequencer_address()` during execution of transactions in that block receives the injected address.

### Likelihood Explanation

Every node that is legitimately elected as proposer for a round can trigger this. No special privilege beyond winning a proposal slot is required. The attack is a single-field modification to the `ProposalInit` message before it is broadcast. Validators have no local reference value to compare `builder` against, so the injected value passes all existing checks and the proposal is accepted by the full committee.

### Recommendation

Add `builder` to `ProposalInitValidation` and check it in `is_proposal_init_valid`, analogously to how `proposer` is checked against the committee:

```rust
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

The `expected_builder` should be derived from the same authoritative source used when building the proposal (e.g., `args.builder_address` in `initiate_build`), stored in `SequencerConsensusContext` and propagated into `ProposalInitValidation` at the point where `validate_current_round_proposal` is called.

### Proof of Concept

1. Node A is elected proposer for height H, round R.
2. Node A constructs `ProposalInit` normally but sets `builder = attacker_address` (any address it controls).
3. Node A broadcasts the proposal stream. The first part is `ProposalPart::Init(init)`.
4. Each validator calls `handle_proposal` → checks `init.proposer` against committee → passes (proposer is correct).
5. Each validator calls `validate_proposal` → `is_proposal_init_valid` → checks height, gas prices, starknet_version, version_constant_commitment, fee_proposal — **never checks `builder`** → passes.
6. `initiate_validation` calls `convert_to_sn_api_block_info(&init)` → `sequencer_address = attacker_address`.
7. Batcher executes all transactions with `sequencer_address = attacker_address`; fees are credited to `attacker_address`.
8. `BlockExecutionArtifacts::new` calls `PartialBlockHashComponents::new(&block_info, ...)` → `sequencer = attacker_address`.
9. `PartialBlockHash::from_partial_block_hash_components` hashes `attacker_address` into the block hash.
10. Consensus reaches decision; the committed block has `sequencer = attacker_address` in its header and block hash.

**Relevant code locations:**

- `ProposalInit.builder` field: [1](#0-0) 
- `is_proposal_init_valid` — no `builder` check anywhere in the function: [2](#0-1) 
- `convert_to_sn_api_block_info` assigns `init.builder` to `sequencer_address`: [3](#0-2) 
- `PartialBlockHashComponents::new` copies `sequencer_address` into `sequencer`: [4](#0-3) 
- `calculate_block_hash` chains `sequencer` into the Poseidon hash: [5](#0-4) 
- `proposer` IS validated (contrast): [6](#0-5) 
- `ProposalInitValidation` struct — no `builder` field: [7](#0-6)

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L107-107)
```rust
    pub builder: ContractAddress,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L252-419)
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
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
    let l1_gas_price_margin_percent =
        VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
    debug!("L1 price info: fri={l1_gas_prices_fri:?}, wei={l1_gas_prices_wei:?}");

    let l1_gas_price_fri = l1_gas_prices_fri.l1_gas_price;
    let l1_data_gas_price_fri = l1_gas_prices_fri.l1_data_gas_price;
    let l1_gas_price_wei = l1_gas_prices_wei.l1_gas_price;
    let l1_data_gas_price_wei = l1_gas_prices_wei.l1_data_gas_price;
    let l1_gas_price_fri_proposed = init_proposed.l1_gas_price_fri;
    let l1_data_gas_price_fri_proposed = init_proposed.l1_data_gas_price_fri;
    let l1_gas_price_wei_proposed = init_proposed.l1_gas_price_wei;
    let l1_data_gas_price_wei_proposed = init_proposed.l1_data_gas_price_wei;

    if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_fri_proposed,
            l1_data_gas_price_fri,
            l1_gas_price_margin_percent,
        )
        && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_wei_proposed,
            l1_data_gas_price_wei,
            l1_gas_price_margin_percent,
        ))
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "L1 gas price mismatch: expected L1 gas price FRI={l1_gas_price_fri}, \
                 proposed={l1_gas_price_fri_proposed}, expected L1 data gas price \
                 FRI={l1_data_gas_price_fri}, proposed={l1_data_gas_price_fri_proposed}, expected \
                 L1 gas price WEI={l1_gas_price_wei}, proposed={l1_gas_price_wei_proposed}, \
                 expected L1 data gas price WEI={l1_data_gas_price_wei}, \
                 proposed={l1_data_gas_price_wei_proposed}, \
                 l1_gas_price_margin_percent={l1_gas_price_margin_percent}"
            ),
        ));
    }

    // fee_proposal is required iff Starknet version >= V0_14_3.
    let fee_proposal_required = init_proposed.starknet_version >= StarknetVersion::V0_14_3;
    match (init_proposed.fee_proposal_fri, fee_proposal_required) {
        (Some(_), false) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal must be absent before V0_14_3, got Some at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        (None, true) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal is required at V0_14_3+, got None at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        _ => {}
    }

    // Validate fee_proposal is within the configured margin of fee_actual.
    // During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
    if let (Some(fee_actual), Some(fee_proposal)) =
        (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
    {
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "Fee proposal out of bounds: fee_actual={}, fee_proposal={}, allowed \
                     range=[{lower_bound}, {upper_bound}]",
                    fee_actual.0, fee_proposal.0
                ),
            ));
        }
    }

    Ok(())
}
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L258-258)
```rust
            .chain(&partial_block_hash_components.sequencer.0)
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
