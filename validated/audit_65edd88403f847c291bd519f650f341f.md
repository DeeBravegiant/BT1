### Title
Unvalidated `builder` Field in `ProposalInit` Allows Proposer to Commit Arbitrary Sequencer Address to Block Header — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit.builder` is a proposer-supplied field that becomes the `sequencer` address in every committed block header. Validators never check it in `is_proposal_init_valid`. A malicious-but-legitimate proposer can set any arbitrary address as `builder`, causing all validators to accept and commit a block header with a wrong sequencer address, corrupting the block hash and potentially misdirecting fee collection.

---

### Finding Description

`ProposalInit` carries two identity fields:

- `proposer` — the consensus identity, verified against the committee in `handle_proposal` before the proposal is even forwarded to the orchestrator.
- `builder` — "Address of the one who builds/sequences the block," set by the proposer from their own static config and **never verified by validators**.

**Proposer side** (`initiate_build`):

```rust
// crates/apollo_consensus_orchestrator/src/build_proposal.rs  line 174
builder: args.builder_address,   // from static_config.builder_address
``` [1](#0-0) 

The `builder_address` is a local static-config value; the TODO comment at the call site explicitly acknowledges it is not yet sourced from the committee:

```rust
// TODO(Asmaa): Get it from committee once we have it.
builder_address: self.config.static_config.builder_address,
``` [2](#0-1) 

**Validator side** (`is_proposal_init_valid`): the function checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, all four L1 gas-price fields, and `fee_proposal_fri` — but **never `builder`**: [3](#0-2) 

`ProposalInitValidation` (the struct that carries the validator's reference values) does not even contain a `builder` field: [4](#0-3) 

**Commitment side**: after a proposal is accepted, `update_state_sync_with_new_block` blindly promotes `init.builder` to the block's `sequencer` address:

```rust
let sequencer = SequencerContractAddress(init.builder);
let block_header_without_hash = BlockHeaderWithoutHash {
    sequencer,
    ...
};
``` [5](#0-4) 

The `sequencer` field is part of `BlockHeaderWithoutHash`, which feeds the block hash calculation. The sequencer address is a direct input to the Starknet block hash. [6](#0-5) 

---

### Impact Explanation

A proposer who is legitimately scheduled for a given height/round can set `ProposalInit.builder` to any address (e.g., an attacker-controlled address). Because validators never check this field, they will:

1. Accept the proposal and execute all transactions against the correct state.
2. Commit a `BlockHeaderWithoutHash` whose `sequencer` field is the attacker-chosen address.
3. Derive a block hash that differs from what an honest proposer would have produced.
4. Persist and broadcast this wrong block hash as the canonical chain tip.

Downstream effects:
- **Wrong block hash** propagated to L1 anchoring, storage, and RPC.
- **Wrong sequencer address** returned by `starknet_getBlockWithTxHashes` and related RPC methods — an authoritative-looking wrong value.
- **Fee misdirection**: in Starknet, transaction fees are transferred to the sequencer address during execution; a wrong `builder` redirects all fees for that block to an attacker-controlled address.

This matches the allowed impact: *"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value"* and *"Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

---

### Likelihood Explanation

The trigger requires the attacker to be the legitimately scheduled proposer for a height/round. In a permissioned or small-committee network this is a regular occurrence. The attack requires no special tooling beyond modifying the `builder_address` field in the node's static config (or patching the binary). No external oracle, no mempool manipulation, no cryptographic break is needed.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and check it in `is_proposal_init_valid`, analogous to how `l2_gas_price_fri` is checked:

```rust
// In ProposalInitValidation
pub expected_builder: ContractAddress,

// In is_proposal_init_valid
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!("builder mismatch: expected={:?}, proposed={:?}",
            proposal_init_validation.expected_builder, init_proposed.builder),
    ));
}
```

The `expected_builder` value should be sourced from the committee (resolving the existing TODO) or, until then, from the validator's own `static_config.builder_address`. This ensures no single proposer can unilaterally redirect the sequencer address.

---

### Proof of Concept

1. Attacker controls a node that is the scheduled proposer for block N.
2. Attacker sets `static_config.builder_address = <attacker_wallet>` in their node config.
3. Node calls `build_proposal` → `initiate_build` → constructs `ProposalInit { builder: <attacker_wallet>, ... }`.
4. `ProposalInit` is broadcast to all validators.
5. Each validator calls `validate_proposal` → `is_proposal_init_valid`: all checked fields pass; `builder` is never read.
6. Validators call `initiate_validation` → batcher executes transactions → `finish_proposal` returns `ProposalCommitment`.
7. Consensus reaches decision; `decision_reached` calls `update_state_sync_with_new_block` with `sequencer = SequencerContractAddress(<attacker_wallet>)`.
8. Block N is committed with the attacker's address as sequencer; all transaction fees for block N are credited to `<attacker_wallet>`; the block hash encodes the wrong sequencer address. [7](#0-6) [8](#0-7) [1](#0-0)

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L394-412)
```rust
        let l1_gas_price = cende_block_info.gas_prices.l1_gas_price_per_token();
        let l1_data_gas_price = cende_block_info.gas_prices.l1_data_gas_price_per_token();
        let l2_gas_price = cende_block_info.gas_prices.l2_gas_price_per_token();
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L806-808)
```rust
            // TODO(Asmaa): Get it from committee once we have it.
            builder_address: self.config.static_config.builder_address,
            cancel_token,
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L1-5)
```rust
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use starknet_types_core::felt::Felt;
```
