### Title
Unvalidated `builder` Address in `ProposalInit` Allows Proposer to Commit Arbitrary Sequencer Address into Block Hash and Fee Transfers — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The `builder` field of `ProposalInit` is mapped directly to `sequencer_address` in the `BlockInfo` forwarded to the batcher, but `is_proposal_init_valid` never checks it against the validator's own expected builder address. A malicious consensus proposer can set `init.builder` to any address; the validator accepts the proposal, the batcher executes the block with that address as sequencer, fees flow to the attacker-controlled address, and the committed block hash encodes the wrong sequencer — all without triggering any existing guard.

---

### Finding Description

**Storage/lookup mismatch (the analog):** In the Mars Red Bank bug, an address is stored under one representation (uppercase) but looked up under another (lowercase), so the same logical entity resolves to two different keys. Here, the `builder` address is *written* into the block hash and fee-transfer logic by the proposer under an arbitrary value, but the validator *reads* it back from the wire without normalizing it against any locally-trusted reference — the same "two representations of the same identity" pattern, applied to the sequencer address slot.

**Root cause — `convert_to_sn_api_block_info`:** [1](#0-0) 

`init.builder` is mapped to `sequencer_address` in the `BlockInfo` that is forwarded to the batcher for both `ProposeBlockInput` (build path) and `ValidateBlockInput` (validate path).

**Propagation into block hash — `PartialBlockHashComponents::new`:** [2](#0-1) 

`block_info.sequencer_address` becomes the `sequencer` field, which is then hashed into the partial block hash: [3](#0-2) 

**Missing guard — `is_proposal_init_valid`:** [4](#0-3) 

The function checks `height`, `l1_da_mode`, `l2_gas_price_fri`, L1 gas prices, `starknet_version`, `version_constant_commitment`, and `fee_proposal_fri`. It does **not** check `init.builder`. The `ProposalInitValidation` struct has no `builder` field: [5](#0-4) 

**Why the `ProposalFinMismatch` check does not catch this:**

Both the proposer and the validator call `convert_to_sn_api_block_info` with the *same* received `init`, so both compute the same (wrong) partial block hash using the attacker-supplied `init.builder`. The commitment comparison at line 244 passes: [6](#0-5) 

**State sync also records the wrong sequencer:** [7](#0-6) 

`sequencer: SequencerContractAddress(init.builder)` is written into `BlockHeaderWithoutHash` and forwarded to state sync.

---

### Impact Explanation

1. **Wrong block hash committed on-chain.** The `sequencer` field is part of `PartialBlockHashComponents` and is Poseidon-hashed into the partial block hash. A wrong `init.builder` produces a wrong partial block hash, which propagates into the final block hash stored on-chain and used as the parent hash of all subsequent blocks — a permanent, chain-wide state corruption.

2. **Fees stolen.** The blockifier uses `block_info.sequencer_address` (= `init.builder`) as the fee recipient for every transaction in the block. A malicious proposer redirects all block fees to an attacker-controlled address.

3. **Wrong sequencer recorded in state sync.** The `BlockHeaderWithoutHash` sent to state sync encodes the attacker-supplied address as the sequencer, corrupting the authoritative block header view returned by RPC.

---

### Likelihood Explanation

Any consensus validator whose turn it is to propose can exploit this. In BFT consensus, up to 1/3 of validators can be malicious, and each receives proposer turns in rotation. No privilege beyond being a scheduled proposer is required. The attack is silent: the `ProposalFinMismatch` guard passes because both sides derive the commitment from the same attacker-supplied `init.builder`.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`:

```rust
// In ProposalInitValidation:
pub builder: ContractAddress,

// In is_proposal_init_valid, alongside the existing height/l1_da_mode/l2_gas_price check:
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

The expected `builder` should be sourced from the node's static config (`consensus_manager_config.context_config.static_config.builder_address`), the same value used in `initiate_build`. [8](#0-7) 

---

### Proof of Concept

1. Malicious validator is scheduled as proposer at height H.
2. In `initiate_build`, the proposer constructs `ProposalInit` with `builder: attacker_address` instead of `args.builder_address`.
3. The proposer broadcasts `ProposalPart::Init(init)` with `init.builder = attacker_address`.
4. The validator receives the init and calls `is_proposal_init_valid` — **passes** (no `builder` check).
5. The validator calls `initiate_validation` → `convert_to_sn_api_block_info(init)` → `sequencer_address = attacker_address`.
6. The batcher validates the block with `sequencer_address = attacker_address`; all transaction fees are transferred to `attacker_address`.
7. `PartialBlockHashComponents::new` encodes `sequencer = attacker_address`; the partial block hash is computed with this wrong value.
8. Both proposer and validator compute the same wrong partial block hash → `ProposalFinMismatch` does **not** trigger.
9. The block is committed: the on-chain block hash encodes `attacker_address` as sequencer, fees are stolen, and all subsequent blocks chain off this corrupted parent hash.

### Citations

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L254-281)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L56-86)
```rust

const GAS_PRICE_ABS_DIFF_MARGIN: u128 = 1;

pub(crate) struct ProposalValidateArguments {
    pub deps: SequencerConsensusContextDeps,
    pub init: ProposalInit,
    pub proposal_init_validation: ProposalInitValidation,
    pub proposal_id: ProposalId,
    pub timeout: Duration,
    pub batcher_timeout_margin: Duration,
    pub valid_proposals: Arc<Mutex<BuiltProposals>>,
    pub content_receiver: mpsc::Receiver<ProposalPart>,
    pub gas_price_params: GasPriceParams,
    pub cancel_token: CancellationToken,
    pub compare_retrospective_block_hash: bool,
}

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L312-321)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L397-412)
```rust
        let sequencer = SequencerContractAddress(init.builder);

        let block_header_without_hash = BlockHeaderWithoutHash {
            block_number: height,
            l1_gas_price,
            l1_data_gas_price,
            l2_gas_price,
            l2_gas_consumed: l2_gas_used,
            next_l2_gas_price: self.l2_gas_price,
            sequencer,
            timestamp: BlockTimestamp(init.timestamp),
            l1_da_mode: init.l1_da_mode,
            fee_proposal_fri: init.fee_proposal_fri,
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };
```

**File:** crates/apollo_node/resources/config_schema.json (L2797-2801)
```json
  "consensus_manager_config.context_config.static_config.builder_address": {
    "description": "The address of the contract that builds the block.",
    "privacy": "Public",
    "value": "0x0"
  },
```
