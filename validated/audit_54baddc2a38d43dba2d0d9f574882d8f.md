### Title
`ProposalInit.builder` Is Not Validated in `is_proposal_init_valid`, Allowing a Malicious Proposer to Inject an Arbitrary `sequencer_address` into Block Execution — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit` carries a `builder` field (the address of the block builder/sequencer). The validator's `is_proposal_init_valid` function checks many `ProposalInit` fields against locally-trusted values, but never checks `builder`. The unchecked `builder` value is then passed verbatim as `sequencer_address` in the `BlockInfo` sent to the blockifier. A legitimate-but-malicious proposer can therefore inject any arbitrary address as the sequencer address, causing wrong fee-transfer state, wrong `get_sequencer_address()` syscall results, and a wrong block hash — all of which are committed by consensus.

---

### Finding Description

`ProposalInit` is defined with a `builder` field:

```rust
/// Address of the one who builds/sequences the block.
pub builder: ContractAddress,
``` [1](#0-0) 

When the honest proposer builds a block, it sets `builder: args.builder_address` from its own local configuration:

```rust
let init = ProposalInit {
    ...
    builder: args.builder_address,
    ...
};
``` [2](#0-1) 

When a validator receives a proposal, it calls `is_proposal_init_valid`. This function validates `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, all four L1 gas prices (within margin), `timestamp` (within window), and `fee_proposal_fri` (within bounds). **`builder` is absent from `ProposalInitValidation` and is never checked:**

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    pub fee_actual: Option<GasPrice>,
    // No `builder` field
}
``` [3](#0-2) 

After `is_proposal_init_valid` passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which maps `init.builder` directly to `sequencer_address`:

```rust
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← proposer-supplied, never validated
    ...
})
``` [4](#0-3) 

This `block_info` is forwarded to the batcher as `ValidateBlockInput.block_info`:

```rust
let input = ValidateBlockInput {
    proposal_id,
    deadline: clock.now() + chrono_timeout,
    retrospective_block_hash: ...,
    block_info: convert_to_sn_api_block_info(init)?,
};
batcher.validate_block(input.clone()).await...
``` [5](#0-4) 

The batcher executes all transactions in the proposal using this `block_info`, meaning the injected `sequencer_address` governs every fee transfer and every `get_sequencer_address()` syscall in the block.

Note that `proposer` **is** checked in `handle_proposal` (line 117 of `single_height_consensus.rs`), confirming the codebase does enforce identity checks on other `ProposalInit` fields — the omission of `builder` is inconsistent. [6](#0-5) 

---

### Impact Explanation

**Wrong state, receipt, event, and block hash from blockifier/execution logic for accepted input (Critical).**

1. **Wrong fee-transfer state**: Every transaction's fee is transferred to `sequencer_address`. With an injected `builder`, all fees in the block go to the attacker's address. The resulting state diff and storage values are wrong.
2. **Wrong receipts/events**: Fee-transfer events carry the wrong recipient address.
3. **Wrong `get_sequencer_address()` syscall result**: Any contract that queries the sequencer address during execution receives the injected value, potentially altering contract logic.
4. **Wrong block hash**: `sequencer_address` is part of the block header and is included in the block hash commitment. The committed `ProposalCommitment` and the L1-anchored block hash both reflect the injected address.

Because the proposer and all validators execute with the same injected `builder`, the `ProposalFinMismatch` check (`built_block != received_fin.proposal_commitment`) does **not** catch this — both sides compute the same (wrong) commitment.

---

### Likelihood Explanation

**Medium.** The attacker must be the elected proposer for a round, which occurs in normal validator rotation. No external or unprivileged trigger is required beyond being a committee member. The attack is silent: the proposal passes all existing validation checks and is committed by honest validators.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`:

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    pub fee_actual: Option<GasPrice>,
+   pub builder: ContractAddress,   // expected builder address from local config
}
```

In `is_proposal_init_valid`:

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

The `builder` value in `ProposalInitValidation` should be populated from the same `builder_address` field used by the proposer in `ProposalBuildArguments`. [7](#0-6) 

---

### Proof of Concept

1. Attacker is elected proposer for round `R` at height `H`.
2. Attacker constructs `ProposalInit` with `builder = attacker_address` (any arbitrary address).
3. Attacker streams `ProposalPart::Init(init)` followed by transactions and `ProposalPart::Fin`.
4. Each validator calls `validate_proposal` → `is_proposal_init_valid`: all checked fields pass; `builder` is never compared to any expected value.
5. `initiate_validation` calls `convert_to_sn_api_block_info(init)` → `sequencer_address = attacker_address`.
6. Batcher executes the block with `sequencer_address = attacker_address`: all fee transfers credit `attacker_address`; `get_sequencer_address()` returns `attacker_address`.
7. Batcher returns `ProposalCommitment` computed over the block with `attacker_address` as sequencer.
8. Proposer's `ProposalFin.proposal_commitment` matches (it was also computed with `attacker_address`), so `ProposalFinMismatch` is not triggered.
9. Consensus reaches decision; the block is committed with wrong state, wrong receipts, and a wrong block hash anchored to L1.

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-107)
```rust
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L70-70)
```rust
    pub builder_address: ContractAddress,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-474)
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
