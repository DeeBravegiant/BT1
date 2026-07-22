### Title
Unvalidated `ProposalInit.builder` field allows a malicious proposer to commit an arbitrary `sequencer_address` into the block hash and expose it to contracts via `get_block_info` — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit` carries two distinct authority addresses: `proposer` (the consensus identity) and `builder` (the block-building sequencer address). The `proposer` field is validated against the committee-derived expected proposer in `single_height_consensus.rs`. The `builder` field is **never validated** anywhere in the proposal-validation path. `convert_to_sn_api_block_info` maps `init.builder` directly to `BlockInfo.sequencer_address`, which is then committed into the partial block hash and exposed to every contract via the `get_block_info` syscall. A malicious proposer can set `builder` to any arbitrary address; all validators will accept the proposal and execute the block with the wrong sequencer address.

---

### Finding Description

`ProposalInit` has two address fields:

```rust
// crates/apollo_protobuf/src/consensus.rs
pub struct ProposalInit {
    pub proposer: ContractAddress,   // validated by consensus layer
    pub builder: ContractAddress,    // NEVER validated
    ...
}
``` [1](#0-0) 

`convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address`:

```rust
// crates/apollo_consensus_orchestrator/src/utils.rs
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← proposer-supplied, unvalidated
    ...
})
``` [2](#0-1) 

This `BlockInfo` is passed to the batcher in both the build path (`propose_block`) and the validate path (`validate_block`): [3](#0-2) [4](#0-3) 

`PartialBlockHashComponents::new` then commits `sequencer_address` into the partial block hash:

```rust
sequencer: SequencerContractAddress(block_info.sequencer_address),
``` [5](#0-4) 

And `calculate_block_hash` chains it into the Poseidon hash:

```rust
.chain(&partial_block_hash_components.sequencer.0)
``` [6](#0-5) 

The `sequencer_address` is also returned to contracts via the `get_block_info` syscall: [7](#0-6) 

`is_proposal_init_valid` validates `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, L1 gas prices, and `fee_proposal_fri` — but **not `builder`**: [8](#0-7) 

`ProposalInitValidation` does not even carry a `builder` field to validate against: [9](#0-8) 

The consensus layer validates `proposer` but not `builder`: [10](#0-9) 

---

### Impact Explanation

**Impact: High/Critical.**

1. **Wrong state committed into block hash.** `sequencer_address` is a direct input to `calculate_block_hash`. A spoofed `builder` produces a different partial block hash, which propagates into the final block hash stored on-chain and in storage. Every downstream consumer of the block hash (L1 anchoring, retrospective block hash, state sync) receives a corrupted value.

2. **Wrong value returned from `get_block_info` syscall.** Every contract that calls `get_block_info` during execution of the accepted block will observe the attacker-chosen `sequencer_address`. Contracts that gate logic on the sequencer address (e.g., fee-collection contracts, operator-gated entry points) will behave incorrectly, producing wrong execution results, receipts, and events.

3. **Wrong `sequencer_address` in `CendeBlockMetadata`.** The Aerospike blob written for the block carries the spoofed address, corrupting the off-chain record.

---

### Likelihood Explanation

**Likelihood: High.** Any node that wins a proposer slot can exploit this. No special privilege is required beyond being the elected proposer for a round. The `builder` field is freely settable in `initiate_build` and is transmitted over the wire in `ProposalPart::Init`. Validators perform no check on it before passing it to the batcher. [11](#0-10) 

---

### Recommendation

Add `builder: ContractAddress` to `ProposalInitValidation` and check it in `is_proposal_init_valid`:

```rust
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

The expected `builder` value should be derived from the same authoritative source used to populate `args.builder_address` in the proposer (e.g., the node's own configured sequencer address, or a committee-registered builder address for the proposer). [12](#0-11) 

---

### Proof of Concept

1. A node is elected proposer for height `H`, round `R`.
2. In `initiate_build`, it constructs `ProposalInit` with `builder: ContractAddress::from(0xdeadbeef)` (arbitrary attacker-chosen address) instead of its legitimate configured address.
3. It streams `ProposalPart::Init(init)` to all validators.
4. Each validator calls `is_proposal_init_valid` — which checks `height`, `l1_da_mode`, `l2_gas_price_fri`, gas prices, `starknet_version`, `version_constant_commitment`, and `fee_proposal_fri`, but **not `builder`** — and returns `Ok(())`.
5. Each validator calls `initiate_validation` → `convert_to_sn_api_block_info(init)` → `BlockInfo { sequencer_address: 0xdeadbeef, ... }` → `batcher.validate_block(input)`.
6. The batcher executes all transactions with `sequencer_address = 0xdeadbeef` in `BlockInfo`. Contracts calling `get_block_info` observe `0xdeadbeef`.
7. `PartialBlockHashComponents::new` commits `sequencer = 0xdeadbeef` into the partial block hash.
8. Consensus reaches decision; the block with the wrong sequencer address is committed to storage and propagated. [13](#0-12) [14](#0-13)

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-347)
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

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L201-206)
```rust
    let build_proposal_input = ProposeBlockInput {
        proposal_id: args.proposal_id,
        deadline: args.batcher_deadline,
        retrospective_block_hash,
        block_info: convert_to_sn_api_block_info(&init)?,
        proposal_round: args.proposal_round,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L253-418)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-466)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-281)
```rust
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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L220-231)
```rust
    fn get_block_info(&self) -> BlockInfo {
        let block_info = match self.base.context.execution_mode {
            ExecutionMode::Execute => self.base.context.tx_context.block_context.block_info(),
            ExecutionMode::Validate => {
                &self.base.context.tx_context.block_context.block_info_for_validate()
            }
        };
        BlockInfo {
            block_number: block_info.block_number.0,
            block_timestamp: block_info.block_timestamp.0,
            sequencer_address: Felt::from(block_info.sequencer_address),
        }
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L114-120)
```rust
        let Ok(proposer_id) = self.committee.get_proposer(height, init.round) else {
            return VecDeque::new();
        };
        if init.proposer != proposer_id {
            warn!("Invalid proposer: expected {:?}, got {:?}", proposer_id, init.proposer);
            return VecDeque::new();
        }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L880-900)
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
