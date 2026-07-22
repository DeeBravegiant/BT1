### Title
Unvalidated `builder` Field in `ProposalInit` Allows Malicious Proposer to Inject Arbitrary Sequencer Address — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_proposal_init_valid` validates most fields of `ProposalInit` but silently skips the `builder` field. Because `builder` is directly mapped to `sequencer_address` in `BlockInfo` and enters the block hash, fee accounting, and `get_execution_info` syscall results, a malicious proposer can set it to any address and have the proposal accepted by every validator.

### Finding Description

`ProposalInit` carries a `builder` field (the sequencer/builder address for the block): [1](#0-0) 

`is_proposal_init_valid` checks `height`, `timestamp`, `starknet_version`, `version_constant_commitment`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, and `fee_proposal_fri`. It never reads or constrains `init_proposed.builder`: [2](#0-1) 

`ProposalInitValidation` — the struct that carries the validator's reference values — has no `builder` field at all, so there is no expected value to compare against: [3](#0-2) 

After `is_proposal_init_valid` returns `Ok`, `convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is handed to the batcher: [4](#0-3) 

That `sequencer_address` then flows into three critical downstream paths:

1. **Block hash** — `PartialBlockHashComponents::new` stores it as `sequencer`, and `calculate_block_hash` chains it into the Poseidon hash: [5](#0-4) 

2. **`get_execution_info` syscall** — contracts read `block_info.sequencer_address` at runtime: [6](#0-5) 

3. **CENDE blob** — `CendeBlockMetadata::new` copies `sequencer_address` into the blob sent to L1: [7](#0-6) 

The proposer sets `builder` from its own configuration (`args.builder_address`): [8](#0-7) 

No other code in the validation path constrains this value.

### Impact Explanation

A malicious proposer sets `builder` to an attacker-controlled address `X`. Every validator runs `is_proposal_init_valid`, finds no check on `builder`, and proceeds. The batcher executes all transactions with `sequencer_address = X`. Consequences:

- **Wrong block hash**: `sequencer_address` is a direct input to the Poseidon block hash; the committed hash diverges from what an honest node would compute.
- **Wrong fee accounting**: transaction fees are transferred to `X` instead of the legitimate sequencer, constituting a direct economic theft of all fees in the block.
- **Wrong `get_execution_info` results**: any contract that reads `block_info.sequencer_address` (e.g., to gate privileged calls) receives `X`, producing incorrect execution outcomes and potentially incorrect reverts or state changes.
- **Wrong CENDE/L1 blob**: the blob carries `sequencer_address = X`, corrupting the L1-anchored record.

### Likelihood Explanation

Any validator that wins a proposal round can trigger this. The attack requires no special privilege beyond being a consensus participant. The modification is a single field in the `ProposalInit` protobuf message sent over the wire; no cryptographic material needs to be forged.

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`:

```rust
// In ProposalInitValidation:
pub builder: ContractAddress,

// In is_proposal_init_valid, alongside the existing height/l1_da_mode/l2_gas_price_fri check:
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

The validator's expected `builder` address is a static configuration value (the sequencer's own address) and is already available at the point where `ProposalInitValidation` is constructed.

### Proof of Concept

1. A malicious validator wins a proposal round.
2. It constructs a `ProposalInit` with all legitimate fields (height, gas prices, etc.) but sets `builder = attacker_address`.
3. It streams this `ProposalInit` followed by a valid transaction batch and `ProposalFin`.
4. Every honest validator calls `is_proposal_init_valid` — no check on `builder` exists, so it returns `Ok`.
5. `initiate_validation` calls `convert_to_sn_api_block_info(init)`, producing `BlockInfo { sequencer_address: attacker_address, … }`.
6. The batcher executes all transactions; fees are transferred to `attacker_address`; `get_execution_info` returns `attacker_address` to every contract.
7. `calculate_block_hash` hashes `attacker_address` as the sequencer, producing a block hash that differs from what an honest proposer would have produced.
8. Consensus reaches decision on this block; the corrupted state is committed.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-107)
```rust
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L231-258)
```rust
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
}

// TODO(Nimrod): Gather the input for this function into a single struct and rename `BlockHashInput`
// => `PythonBlockHashInput`.
/// Poseidon (
///     block_hash_constant, block_number, global_state_root, sequencer_address,
///     block_timestamp, concat_counts, state_diff_hash, transaction_commitment,
///     event_commitment, receipt_commitment, gas_prices, starknet_version, 0, parent_block_hash
/// ).
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
```

**File:** crates/starknet_os/src/hints/hint_implementation/block_context.rs (L25-26)
```rust
            ("sequencer_address", (**block_info.sequencer_address).into()),
        ],
```

**File:** crates/apollo_batcher/src/cende_client_types.rs (L572-574)
```rust
            timestamp: block_info.block_timestamp,
            sequencer_address: block_info.sequencer_address,
        }
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L174-174)
```rust
        builder: args.builder_address,
```
