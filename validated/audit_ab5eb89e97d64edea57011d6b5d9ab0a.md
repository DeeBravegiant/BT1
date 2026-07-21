### Title
`ProposalInit.builder` Not Validated in `is_proposal_init_valid()`, Allowing Arbitrary Sequencer Address in Committed Block — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit` carries two identity fields: `proposer` (the consensus-round proposer, checked against the committee) and `builder` (the address that becomes `sequencer_address` in block execution and the block hash). `is_proposal_init_valid()` validates `proposer` indirectly via `manager.rs`, but **never validates `builder`**. A malicious proposer can set `builder` to any arbitrary address. The validator accepts the proposal, executes transactions with the forged `sequencer_address`, and commits a block whose hash encodes the wrong sequencer — corrupting every `get_sequencer_address()` syscall result and the on-chain block hash.

---

### Finding Description

`ProposalInit` has two address fields:

```
pub proposer: ContractAddress,  // who proposed in consensus
pub builder: ContractAddress,   // who builds/sequences the block
``` [1](#0-0) 

The `proposer` field is checked in `manager.rs` against the committee-derived expected proposer:

```rust
if proposer != init.proposer {
    warn!("Invalid proposer for height {} and round {}: expected {:?}, got {:?}", ...);
    return Ok(VecDeque::new());
}
``` [2](#0-1) 

`is_proposal_init_valid()` validates `timestamp`, `starknet_version`, `version_constant_commitment`, `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, and `fee_proposal_fri`. It does **not** validate `builder` at all: [3](#0-2) 

After `is_proposal_init_valid()` passes, `initiate_validation()` calls `convert_to_sn_api_block_info(init)`, which maps `init.builder` directly to `sequencer_address`:

```rust
sequencer_address: init.builder,
``` [4](#0-3) 

This `block_info` (with the forged `sequencer_address`) is sent to the batcher via `ValidateBlockInput`. The batcher executes all transactions under this block context and computes `PartialBlockHashComponents`:

```rust
sequencer: SequencerContractAddress(block_info.sequencer_address),
``` [5](#0-4) 

The `sequencer` field is then chained into the block hash:

```rust
.chain(&partial_block_hash_components.sequencer.0)
``` [6](#0-5) 

The `ProposalCommitment` comparison at the end of `validate_proposal()` compares the validator's computed commitment (derived using the proposer-supplied `builder`) against the proposer's claimed commitment (also derived using the same `builder`). Both sides use the forged value, so they match and the proposal is accepted:

```rust
if built_block != received_fin.proposal_commitment {
    return Err(ValidateProposalError::ProposalFinMismatch);
}
``` [7](#0-6) 

---

### Impact Explanation

**Impact: High/Critical — Wrong state from blockifier/syscall/execution logic for accepted input.**

1. **Wrong `get_sequencer_address()` syscall result**: Every contract that calls `get_sequencer_address()` during execution receives the forged address. Access-control logic, fee-routing contracts, or any protocol that gates on the sequencer address will behave incorrectly.

2. **Wrong block hash committed on-chain**: The sequencer address is a direct input to `calculate_block_hash()`. A forged `builder` produces a block hash that does not correspond to the honest sequencer, permanently corrupting the L2 chain's hash chain and any L1 anchoring that depends on it.

3. **Fee misdirection**: Sequencer fees collected during execution are attributed to the forged `builder` address, not the legitimate sequencer.

---

### Likelihood Explanation

Requires a Byzantine proposer — a validator in the active committee who deliberately sets `builder` to an arbitrary address. In a BFT system with up to `f < n/3` Byzantine validators, this is within the threat model. The attack requires no special tooling: the proposer simply serializes a `ProposalInit` with a modified `builder` field. No existing guard in `is_proposal_init_valid()` or `initiate_validation()` catches it.

---

### Recommendation

Add a check in `is_proposal_init_valid()` (or in `validate_current_round_proposal()` before dispatching) that enforces `init_proposed.builder` equals the locally-known expected builder address. The simplest form mirrors the `proposer` check already present in `manager.rs`:

```rust
// In is_proposal_init_valid or ProposalInitValidation:
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!(
            "builder mismatch: expected={:?}, proposed={:?}",
            proposal_init_validation.expected_builder, init_proposed.builder
        ),
    ));
}
```

`ProposalInitValidation` should carry `expected_builder: ContractAddress`, populated from the node's own configured `builder_address` (the same value used in `initiate_build()`). [8](#0-7) [9](#0-8) 

---

### Proof of Concept

1. A malicious proposer is selected for height `H`, round `R`.
2. In `initiate_build()`, instead of setting `builder: args.builder_address`, the proposer sets `builder: ContractAddress::from(0xdeadbeef)` (any arbitrary address).
3. The forged `ProposalInit` is streamed to all validators.
4. Each validator calls `validate_proposal()` → `is_proposal_init_valid()`. No check on `builder` exists; the function returns `Ok(())`.
5. `initiate_validation()` calls `convert_to_sn_api_block_info(init)`, producing `block_info.sequencer_address = 0xdeadbeef`.
6. The batcher executes all transactions with `sequencer_address = 0xdeadbeef`. Any contract calling `get_sequencer_address()` receives `0xdeadbeef`.
7. `PartialBlockHashComponents` encodes `sequencer = 0xdeadbeef`. The validator's computed `ProposalCommitment` matches the proposer's (both use the same forged value). `ProposalFinMismatch` is not triggered.
8. Consensus reaches decision; the block is committed with `sequencer_address = 0xdeadbeef` in the block hash and execution context.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L95-128)
```rust
pub struct ProposalInit {
    /// The height of the consensus (block number).
    pub height: BlockNumber,
    /// The current round of the consensus.
    pub round: Round,
    /// The last round that was valid.
    pub valid_round: Option<Round>,
    /// Address of the one who proposed the block in consensus.
    pub proposer: ContractAddress,
    /// Block timestamp.
    pub timestamp: u64,
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
    /// L1 data availability mode.
    pub l1_da_mode: L1DataAvailabilityMode,
    /// L2 gas price in FRI.
    pub l2_gas_price_fri: GasPrice,
    /// L1 gas price in FRI.
    pub l1_gas_price_fri: GasPrice,
    /// L1 data gas price in FRI.
    pub l1_data_gas_price_fri: GasPrice,
    // Keeping the wei prices for now, to use with L1 transactions.
    /// L1 gas price in WEI.
    pub l1_gas_price_wei: GasPrice,
    /// L1 data gas price in WEI.
    pub l1_data_gas_price_wei: GasPrice,
    /// Starknet protocol version.
    pub starknet_version: starknet_api::block::StarknetVersion,
    /// Version constant commitment.
    pub version_constant_commitment: StarkHash,
    /// Proposer's oracle-derived recommended L2 gas fee. Present iff
    /// `starknet_version >= V0_14_3`.
    pub fee_proposal_fri: Option<GasPrice>,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L73-85)
```rust
// Contains parameters required for validating ProposalInit.
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-282)
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
