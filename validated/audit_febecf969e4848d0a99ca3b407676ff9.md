### Title
Unvalidated `ProposalInit.builder` Field Allows Malicious Proposer to Redirect Block Fees to Arbitrary Address — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

The `builder` field of `ProposalInit` — which becomes `sequencer_address` in `BlockInfo` and is therefore the fee-collection address for every transaction in the block — is never checked by `is_proposal_init_valid`. A validator who wins the proposer slot can set `init.builder` to any address they control, silently redirecting all transaction fees for that block to themselves while every other validator accepts the proposal as valid.

### Finding Description

**Root cause — `builder` is absent from `ProposalInitValidation`**

`ProposalInitValidation` carries every field that `is_proposal_init_valid` enforces: [1](#0-0) 

`builder` is not among them. The full validation block checks `height`, `l1_da_mode`, `l2_gas_price_fri`, L1 gas prices, `starknet_version`, `version_constant_commitment`, `timestamp`, and `fee_proposal_fri`: [2](#0-1) 

`init.builder` is never read inside `is_proposal_init_valid`.

**How `builder` reaches fee collection**

In `initiate_build`, the proposer freely sets `builder: args.builder_address`: [3](#0-2) 

`convert_to_sn_api_block_info` then maps `init.builder` directly to `sequencer_address`: [4](#0-3) 

`sequencer_address` is the Starknet fee-collection address: every fee paid by every transaction in the block is transferred to this address during execution.

**`builder` also enters the block hash**

`PartialBlockHashComponents::new` stores `sequencer_address` as the `sequencer` field: [5](#0-4) 

`calculate_block_hash` chains it into the Poseidon hash: [6](#0-5) 

So an attacker-controlled `builder` value corrupts both the fee recipient and the canonical block hash.

**Validation path that should catch this but does not**

`validate_proposal` calls `is_proposal_init_valid` and then `initiate_validation`, which passes `convert_to_sn_api_block_info(init)` (containing the unchecked `builder`) straight to the batcher: [7](#0-6) [8](#0-7) 

The batcher executes the block with the attacker-supplied `sequencer_address` without any further check.

**Contrast with `proposer` validation**

The consensus manager does validate `init.proposer` against the committee-elected proposer: [9](#0-8) 

`init.builder` receives no equivalent treatment.

### Impact Explanation

Every transaction fee in the affected block is transferred to the attacker-controlled address instead of the legitimate sequencer. Because `sequencer_address` is also hashed into `PartialBlockHashComponents`, the committed block hash encodes the wrong sequencer, producing an authoritative-looking but incorrect state commitment. This matches the allowed impact: **Critical — incorrect fee/balance effect with economic impact** and **wrong state/block hash from execution logic for accepted input**.

### Likelihood Explanation

The attacker must be selected as proposer for at least one height, which requires holding a validator slot. Once selected, the attack is a single-field substitution in `ProposalInit` — no additional privileges, no race conditions, and no detectable deviation from the protocol as currently validated. Every other validator will accept the proposal.

### Recommendation

1. Add a `builder: ContractAddress` field to `ProposalInitValidation`, populated from the node's own configured sequencer address.
2. In `is_proposal_init_valid`, assert `init_proposed.builder == proposal_init_validation.builder` and return `InvalidProposalInit` on mismatch.
3. Ensure `ProposalBuildArguments::builder_address` is sourced from the same authoritative configuration so proposer and validator agree on the expected value.

### Proof of Concept

```
1. Attacker A holds a validator key and is elected proposer for block N.
2. A calls build_proposal; in initiate_build, sets init.builder = A_fee_wallet.
3. ProposalInit { builder: A_fee_wallet, ... } is streamed to all validators.
4. Each validator calls is_proposal_init_valid — builder is never checked → Ok(()).
5. initiate_validation passes convert_to_sn_api_block_info(init) to the batcher
   with sequencer_address = A_fee_wallet.
6. Blockifier executes all transactions; every fee is transferred to A_fee_wallet.
7. PartialBlockHashComponents encodes A_fee_wallet as sequencer; the committed
   block hash is accepted by consensus and written to storage.
8. Result: A steals all transaction fees for block N; the canonical block hash
   encodes the wrong sequencer address.
```

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L153-171)
```rust
    is_proposal_init_valid(
        &args.proposal_init_validation,
        &args.init,
        args.deps.clock.as_ref(),
        args.deps.l1_gas_price_provider,
        &args.gas_price_params,
    )
    .await?;

    initiate_validation(
        args.deps.batcher.clone(),
        args.deps.state_sync_client,
        &args.init,
        args.proposal_id,
        args.timeout + args.batcher_timeout_margin,
        args.deps.clock.as_ref(),
        args.compare_retrospective_block_hash,
    )
    .await?;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L253-321)
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
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-475)
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
    Ok(())
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
