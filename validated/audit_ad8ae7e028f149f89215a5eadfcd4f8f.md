### Title
Unvalidated `ProposalInit.builder` Field Allows Malicious Proposer to Redirect All Block Fees and Corrupt Sequencer-Address Syscall Results - (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit` carries a `builder` field (the sequencer/block-builder address). Every other security-sensitive field in `ProposalInit` is validated by `is_proposal_init_valid` against a locally-trusted reference. The `builder` field is never validated. A Byzantine proposer can set `builder` to any arbitrary address; all honest validators will accept the proposal, execute the block with the attacker-controlled sequencer address, and commit a block in which all transaction fees are transferred to the attacker's address and every `get_block_info` syscall returns the wrong sequencer address.

---

### Finding Description

`ProposalInit` is defined with a `builder` field:

```rust
pub struct ProposalInit {
    pub proposer: ContractAddress,
    pub builder: ContractAddress,   // "Address of the one who builds/sequences the block"
    ...
}
``` [1](#0-0) 

When a validator receives a proposal, `validate_proposal` calls `is_proposal_init_valid` and then `initiate_validation`. Inside `initiate_validation`, `convert_to_sn_api_block_info(init)` is called, which maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is forwarded to the batcher for execution:

```rust
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // <-- proposer-supplied, never validated
    ...
})
``` [2](#0-1) 

`is_proposal_init_valid` validates `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices (within margin), `starknet_version`, `version_constant_commitment`, and `fee_proposal_fri`. It does **not** validate `builder`: [3](#0-2) 

`ProposalInitValidation` — the struct that carries the locally-trusted reference values — has no `builder` or `sequencer_address` field at all: [4](#0-3) 

The proposer legitimately sets `builder: args.builder_address` from its own configuration: [5](#0-4) 

A Byzantine proposer can instead set `builder` to any address. Because both the proposer and every validator use the same `init.builder` value when computing the `partial_block_hash` via `PartialBlockHashComponents::new`:

```rust
sequencer: SequencerContractAddress(block_info.sequencer_address),
``` [6](#0-5) 

…both sides derive the same `ProposalCommitment`, so the `ProposalFinMismatch` guard does not fire: [7](#0-6) 

The block is committed with the attacker-supplied sequencer address.

---

### Impact Explanation

**Fee theft (economic impact).** The `sequencer_address` in `BlockInfo` is the address to which all transaction fees are transferred during execution (`complete_fee_transfer_flow`). By setting `builder` to an attacker-controlled address, the Byzantine proposer redirects every fee payment in the block to themselves. All honest validators accept and vote for the proposal, so the block is finalized with the stolen fees.

**Wrong `get_block_info` syscall result.** Every contract that calls the `get_block_info` syscall receives the attacker-supplied address as `sequencer_address`. Contract logic that branches on the sequencer address (e.g., fee-market contracts, access-control logic) will observe a wrong value, producing incorrect execution results that are committed to state.

**Wrong partial block hash / state commitment.** The `sequencer_address` is a direct input to `PartialBlockHashComponents` and therefore to the `partial_block_hash` and the final `ProposalCommitment`. The committed block hash reflects the attacker's address, not the legitimate sequencer address.

---

### Likelihood Explanation

The attacker must be a validator that is selected as the proposer for a given height/round. In a BFT system with up to `f` Byzantine validators, any Byzantine validator that wins the proposer lottery can execute this attack. No other precondition is required: the attack is a single-field substitution in the `ProposalInit` message, requires no special permissions beyond being a selected proposer, and is completely invisible to all honest validators because no validation of `builder` exists anywhere in the proposal-validation path.

---

### Recommendation

Add `builder` (the expected local sequencer address) to `ProposalInitValidation` and reject any proposal whose `init_proposed.builder` does not match:

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub builder: ContractAddress,   // add this
    ...
}
```

In `is_proposal_init_valid`, add:

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

Populate `ProposalInitValidation::builder` from the node's own configured sequencer/builder address in `validate_proposal` inside `SequencerConsensusContext`. [8](#0-7) 

---

### Proof of Concept

1. A Byzantine validator is selected as proposer for height H.
2. It constructs a `ProposalInit` with all fields matching what honest validators expect **except** `builder`, which is set to `attacker_address`.
3. It streams `ProposalPart::Init(init)` followed by valid transaction batches and a `ProposalFin` whose `proposal_commitment` is computed using `attacker_address` as `sequencer_address` (matching what validators will compute).
4. Each honest validator calls `is_proposal_init_valid` — passes, because `builder` is not checked.
5. Each honest validator calls `initiate_validation` → `convert_to_sn_api_block_info(init)` → batcher executes the block with `sequencer_address = attacker_address`.
6. The batcher returns `partial_block_hash` computed with `attacker_address`; `proposal_commitment_from` produces the same commitment as the proposer's `ProposalFin.proposal_commitment` — no `ProposalFinMismatch`.
7. `validate_proposal` returns `Ok(built_block)`. Consensus votes proceed. The block is finalized.
8. All transaction fees in block H are in `attacker_address`. Every contract that called `get_block_info` during block H received `attacker_address` as `sequencer_address`. The committed partial block hash encodes `attacker_address`.

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
