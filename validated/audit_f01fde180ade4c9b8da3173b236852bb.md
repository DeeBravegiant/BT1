### Title
Unvalidated `builder` Field in `ProposalInit` Allows Malicious Proposer to Inject Arbitrary Sequencer Address into Block Context — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_proposal_init_valid` validates most fields of a received `ProposalInit` (height, L1/L2 gas prices, timestamp, starknet version, fee proposal, version constant commitment) but never checks the `builder` field. `convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is handed to the batcher. The batcher uses that `BlockInfo` to build the block context, which governs the `get_sequencer_address` syscall result and is hashed into `PartialBlockHashComponents`. A validator node accepts and commits any `builder` value the proposer supplies, producing a block whose sequencer address, syscall outputs, and partial block hash all reflect the attacker-chosen value.

---

### Finding Description

**Root cause — missing field in `ProposalInitValidation` and `is_proposal_init_valid`**

`ProposalInitValidation` carries the locally-derived reference values used to check a received `ProposalInit`:

```
height, block_timestamp_window_seconds, previous_proposal_init,
l1_da_mode, l2_gas_price_fri, starknet_version, fee_actual
``` [1](#0-0) 

`builder` is absent. `is_proposal_init_valid` therefore never compares `init_proposed.builder` against any expected value. [2](#0-1) 

**Propagation — `builder` becomes `sequencer_address`**

`convert_to_sn_api_block_info` maps the unchecked field directly:

```rust
sequencer_address: init.builder,
``` [3](#0-2) 

This `BlockInfo` is passed to the batcher via `ValidateBlockInput` in `initiate_validation`: [4](#0-3) 

**Commitment path — `sequencer_address` enters the block hash**

`PartialBlockHashComponents::new` copies `block_info.sequencer_address` into the `sequencer` field, which `calculate_block_hash` chains into the Poseidon hash: [5](#0-4) [6](#0-5) 

**Proposer-side sets `builder` from local config; no cross-node constraint exists**

```rust
builder: args.builder_address,   // = self.config.static_config.builder_address
``` [7](#0-6) 

The proposer check in `manager.rs` validates `init.proposer` against the committee-derived leader but says nothing about `init.builder`: [8](#0-7) 

Because both proposer and validator derive the `PartialBlockHash` from the same (unvalidated) `builder` value, the `ProposalFinMismatch` guard does not fire: [9](#0-8) 

---

### Impact Explanation

A validator node that is the legitimate proposer for a height/round can set `ProposalInit.builder` to any `ContractAddress`. Every other validator will:

1. Execute the block with `sequencer_address` = attacker-chosen value → `get_sequencer_address` syscall returns the wrong address for every transaction in the block.
2. Compute `PartialBlockHashComponents` with the wrong `sequencer` field → the committed `PartialBlockHash` and the final `BlockHash` are wrong.
3. Commit the block to storage with the wrong sequencer address in the block header.
4. Forward the wrong `sequencer_address` to the cende blob pipeline and state sync.

Fee accounting in Starknet credits the sequencer address; a malicious proposer pointing `builder` at their own address redirects block fees to themselves.

This matches the allowed impact: **wrong state/receipt/event/storage value from blockifier/syscall/execution logic** and **incorrect fee/balance effect with economic impact**.

---

### Likelihood Explanation

The attacker must be the elected proposer for a height/round. In the current trusted-validator deployment this is a committee member. As the network decentralises (any staked validator can be elected), any validator becomes a potential attacker. The attack requires no special tooling beyond modifying the `builder_address` config field before proposing.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`. The expected value should be derived from the committee (analogous to how `proposer` is derived) or from a protocol-level registry. At minimum, the validator should reject any `builder` value that differs from the locally configured `builder_address` (or from the committee-supplied builder for that height/round once that information is available).

```rust
// In ProposalInitValidation:
pub expected_builder: ContractAddress,

// In is_proposal_init_valid:
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!("builder mismatch: expected={:?}, got={:?}",
            proposal_init_validation.expected_builder, init_proposed.builder),
    ));
}
```

---

### Proof of Concept

1. A validator node is elected proposer for height H, round R.
2. Before calling `build_proposal`, the node sets `config.static_config.builder_address` to an attacker-controlled address `ATTACKER_ADDR`.
3. `initiate_build` constructs `ProposalInit { builder: ATTACKER_ADDR, … }` and broadcasts it.
4. Every peer validator calls `validate_proposal` → `is_proposal_init_valid`. The function checks height, gas prices, timestamp, starknet version, fee proposal — but never `builder`. Validation passes.
5. `initiate_validation` calls `convert_to_sn_api_block_info` which sets `sequencer_address = ATTACKER_ADDR` in the `BlockInfo` sent to the batcher.
6. The batcher executes all transactions with `sequencer_address = ATTACKER_ADDR`. Any `get_sequencer_address` syscall returns `ATTACKER_ADDR`. Fees are credited to `ATTACKER_ADDR`.
7. `PartialBlockHashComponents` is computed with `sequencer = ATTACKER_ADDR`. The `PartialBlockHash` and final `BlockHash` embed the wrong sequencer.
8. Both proposer and validator compute the same (wrong) hash, so `ProposalFinMismatch` does not trigger. The block is committed with the wrong sequencer address and wrong block hash.

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
