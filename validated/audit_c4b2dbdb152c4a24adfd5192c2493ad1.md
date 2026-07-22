### Title
Unvalidated `ProposalInit.builder` Used as Fee-Recipient `sequencer_address` Allows Malicious Proposer to Redirect All Block Fees — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`, `crates/apollo_consensus_orchestrator/src/utils.rs`)

---

### Summary

`is_proposal_init_valid` validates many fields of the proposer-supplied `ProposalInit` (height, L1/L2 gas prices, DA mode, starknet version, fee proposal) but never validates `init.builder`. That field is then passed verbatim as `sequencer_address` in `BlockInfo` via `convert_to_sn_api_block_info`. Because `sequencer_address` is the ERC-20 fee-transfer recipient for every transaction in the block, a malicious proposer can set `builder` to any address and redirect the entire block's fee revenue to an attacker-controlled account. Validators accept the proposal, the batcher executes all fee transfers to the wrong address, and the block is committed with the corrupted state.

---

### Finding Description

**Step 1 – `builder` is proposer-supplied and unvalidated.**

`ProposalInit.builder` is set by the proposer in `initiate_build`:

```rust
// crates/apollo_consensus_orchestrator/src/build_proposal.rs:174
builder: args.builder_address,
```

It is transmitted over the wire as a plain protobuf `Address` field with no cryptographic binding to the proposer's identity.

**Step 2 – `is_proposal_init_valid` never checks `builder`.**

The full validation in `is_proposal_init_valid` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `l1_gas_price_{fri,wei}`, `l1_data_gas_price_{fri,wei}`, `starknet_version`, `version_constant_commitment`, and `fee_proposal_fri`. The `builder` (and `proposer`) fields are absent from `ProposalInitValidation` and are never compared against any locally-trusted reference:

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs:312-321
if !(init_proposed.height == proposal_init_validation.height
    && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
    && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri)
{
    return Err(ValidateProposalError::InvalidProposalInit(...));
}
// No check on init_proposed.builder
```

**Step 3 – `builder` becomes `sequencer_address` (fee recipient) for the entire block.**

After `is_proposal_init_valid` passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`:

```rust
// crates/apollo_consensus_orchestrator/src/utils.rs:329-332
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← attacker-controlled
    ...
})
```

**Step 4 – Every fee transfer in the block sends tokens to `sequencer_address`.**

```rust
// crates/blockifier/src/transaction/account_transaction.rs:571-573
calldata: calldata![
    *block_context.block_info.sequencer_address.0.key(), // Recipient.
    lsb_amount,
    msb_amount
],
```

**Step 5 – `ProposalCommitment` comparison does not catch the manipulation.**

Both the proposer and the validator compute the block hash using the same `init.builder` value, so `built_block == received_fin.proposal_commitment` holds and the block is accepted and committed.

---

### Impact Explanation

**Impact: Critical — Incorrect fee/balance effect with economic impact.**

A malicious proposer sets `init.builder = attacker_address`. Every transaction fee in the block is transferred to `attacker_address` instead of the legitimate sequencer. The state diff committed to L1 reflects the wrong fee-token storage updates. The legitimate sequencer earns zero fees for the block. The attacker gains the full block reward without any on-chain restriction.

---

### Likelihood Explanation

**Likelihood: Medium.**

Any node that wins a proposer slot (round-robin or stake-weighted) can exploit this. No special privilege beyond being the designated proposer for one round is required. The attack is silent: the block passes all existing validation checks, the `ProposalCommitment` matches, and no error is logged. The only observable effect is the wrong fee-token storage entries in the committed state diff.

---

### Recommendation

Add `builder` (and `proposer`) to `ProposalInitValidation` and enforce them in `is_proposal_init_valid`. The validator already knows the expected proposer for the current round from the consensus layer; it should also know the expected builder address (its own `builder_address` config, or a committee-derived value). Concretely:

1. Add `expected_builder: ContractAddress` to `ProposalInitValidation`.
2. In `is_proposal_init_valid`, assert `init_proposed.builder == proposal_init_validation.expected_builder`.
3. Populate `expected_builder` from `self.config.static_config.builder_address` when constructing `ProposalInitValidation` in `validate_current_round_proposal`.

This mirrors the fix recommended in the external report: the "from" of a privileged transfer must always be a locally-authenticated value, never an arbitrary input parameter.

---

### Proof of Concept

```
1. Malicious node M wins proposer slot for height H, round R.

2. M constructs ProposalInit {
       height: H,
       round: R,
       builder: ATTACKER_ADDRESS,   // ← arbitrary, not the real sequencer
       l2_gas_price_fri: <valid>,
       l1_gas_price_fri: <within margin>,
       ...                          // all other fields pass is_proposal_init_valid
   }

3. Validator V receives the ProposalInit.
   - is_proposal_init_valid() passes (builder is never checked).
   - initiate_validation() calls convert_to_sn_api_block_info(init):
       BlockInfo { sequencer_address: ATTACKER_ADDRESS, ... }
   - Batcher executes N transactions; each fee transfer sends tokens to ATTACKER_ADDRESS.

4. M sends ProposalFin with proposal_commitment = hash(block built with ATTACKER_ADDRESS).
   V computes the same hash (same builder), so built_block == received_fin.proposal_commitment.
   Proposal is accepted and committed.

5. Result: ATTACKER_ADDRESS receives all N transaction fees.
           Legitimate sequencer receives zero fees for block H.
           State diff committed to L1 contains wrong fee-token storage entries.
```

**Relevant code locations:**

- `is_proposal_init_valid` (no `builder` check): [1](#0-0) 
- `convert_to_sn_api_block_info` (`builder` → `sequencer_address`): [2](#0-1) 
- `execute_fee_transfer` (fee sent to `sequencer_address`): [3](#0-2) 
- `ProposalInitValidation` struct (no `builder` field): [4](#0-3) 
- `ProposalInit.builder` definition: [5](#0-4) 
- Proposer sets `builder` from local config: [6](#0-5)

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L329-333)
```rust
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L570-578)
```rust
            entry_point_selector: selector_from_name(constants::TRANSFER_ENTRY_POINT_NAME),
            calldata: calldata![
                *block_context.block_info.sequencer_address.0.key(), // Recipient.
                lsb_amount,
                msb_amount
            ],
            storage_address,
            caller_address: tx_info.sender_address(),
            call_type: CallType::Call,
```

**File:** crates/apollo_protobuf/src/consensus.rs (L103-107)
```rust
    pub proposer: ContractAddress,
    /// Block timestamp.
    pub timestamp: u64,
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
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
