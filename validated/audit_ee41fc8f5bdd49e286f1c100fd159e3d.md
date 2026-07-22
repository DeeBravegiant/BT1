### Title
`is_proposal_init_valid` checks only a subset of `ProposalInit` fields, leaving `builder` (sequencer address) unvalidated — a malicious proposer can redirect all block fees to an arbitrary address - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_proposal_init_valid` validates `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, `starknet_version`, `version_constant_commitment`, `fee_proposal_fri`, and `timestamp` from a received `ProposalInit`, but silently skips the `builder` field. `builder` is passed verbatim as `sequencer_address` into `BlockInfo` and used by the blockifier for all fee collection in the block. Any consensus proposer can set `builder` to an attacker-controlled address; validators accept the proposal because the field is never checked, and the committed block collects every transaction fee to the wrong address.

---

### Finding Description

`ProposalInit` carries a `builder` field documented as "Address of the one who builds/sequences the block." [1](#0-0) 

`is_proposal_init_valid` performs a multi-field check at lines 312–321, but the compound condition only covers `height`, `l1_da_mode`, and `l2_gas_price_fri`: [2](#0-1) 

`builder` is absent from every branch of `is_proposal_init_valid` and is also absent from `ProposalInitValidation`, the struct that carries the validator's reference values: [3](#0-2) 

After `is_proposal_init_valid` returns `Ok`, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which maps `init.builder` directly to `sequencer_address`: [4](#0-3) 

This `BlockInfo` is forwarded to the batcher via `ValidateBlockInput`. The batcher executes every transaction in the block using this `sequencer_address` for fee collection. Because both the proposer and the validator derive `BlockInfo` from the same wire `ProposalInit`, the `proposal_commitment` comparison at the end of `validate_proposal` passes: [5](#0-4) 

The block is therefore committed with the attacker-chosen sequencer address, and all fees flow to it.

The proposer sets `builder` to its own `builder_address` during `initiate_build`: [6](#0-5) 

Nothing prevents a malicious proposer from substituting any other address in that field before broadcasting.

This is the direct structural analog of the external bug: `ThrusterTreasure.enterTickets()` checked only `winningTickets[currentRound_][0].length` while ignoring higher-index prizes; here, `is_proposal_init_valid` checks `height`/`l1_da_mode`/`l2_gas_price_fri` while ignoring `builder` — a field in the same struct that has equal or greater economic consequence.

---

### Impact Explanation

`sequencer_address` in Starknet `BlockInfo` is the address that receives all transaction fees for the block. By setting `builder` to an address they control, a malicious proposer silently redirects 100 % of block fees. The committed state is wrong (fees credited to the wrong account), and the effect is permanent once the block is finalized. The magnitude equals the total fee revenue of the manipulated block, which is unbounded.

---

### Likelihood Explanation

The attacker must be selected as the consensus proposer for at least one round. In a BFT validator set, every validator is eligible to propose; no additional privilege is required. The manipulation is invisible to other validators because `builder` is never compared against any reference value during validation, and the `proposal_commitment` still matches.

---

### Recommendation

1. Add `builder: ContractAddress` to `ProposalInitValidation`, populated from the validator's own known sequencer address (or the committee-derived expected builder).
2. In `is_proposal_init_valid`, extend the existing compound check to include `builder`:

```rust
if !(init_proposed.height == proposal_init_validation.height
    && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
    && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri
    && init_proposed.builder == proposal_init_validation.builder)   // ADD THIS
{
    return Err(ValidateProposalError::InvalidProposalInit(...));
}
```

3. Populate `proposal_init_validation.builder` at both call sites in `sequencer_consensus_context.rs` (lines 880–900 and 1178–1198) from the context's own `builder_address`.

---

### Proof of Concept

1. Malicious validator **M** is selected as proposer for round R at height H.
2. M constructs a `ProposalInit` with `builder = M_fee_wallet` (an address M controls) and all other fields valid.
3. M broadcasts the proposal stream to peer validators.
4. Each validator calls `is_proposal_init_valid` — `builder` is not in `ProposalInitValidation` and is not checked anywhere in the function; validation returns `Ok`.
5. `initiate_validation` calls `batcher.validate_block` with `block_info.sequencer_address = M_fee_wallet`.
6. The batcher executes all transactions, crediting fees to `M_fee_wallet`.
7. The batcher returns `partial_block_hash` computed over the state that includes fees at `M_fee_wallet`; M's proposer side computed the same hash using the same `builder`, so `built_block == received_fin.proposal_commitment` — the fin check passes.
8. Consensus reaches decision; the block is committed. All fees for height H reside at `M_fee_wallet` instead of the legitimate sequencer address.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-108)
```rust
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
    /// L1 data availability mode.
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-247)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L329-333)
```rust
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
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
