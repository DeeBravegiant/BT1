### Title
Unvalidated `ProposalInit.builder` Field Allows Malicious Proposer to Redirect Sequencer Fees and Corrupt Block Header `sequencer_address` - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The `is_proposal_init_valid` function in the proposal validation path does not check `ProposalInit.builder` against any locally-trusted value. A Byzantine proposer can set `builder` to an arbitrary contract address. The validator accepts it, passes it verbatim as `sequencer_address` into `BlockInfo`, executes the block under that address, and commits the result. All sequencer fees for the block are credited to the attacker-controlled address, and the committed block header carries the wrong `sequencer_address`.

---

### Finding Description

`ProposalInit` carries a `builder` field (the address of the block-building sequencer node). During validation, `is_proposal_init_valid` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices (within margin), `starknet_version`, `version_constant_commitment`, `timestamp`, and `fee_proposal_fri`. It does **not** check `builder`. [1](#0-0) 

`ProposalInitValidation` — the struct that carries the locally-trusted reference values — has no `builder` field at all: [2](#0-1) 

After `is_proposal_init_valid` passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which maps `init.builder` directly to `sequencer_address` in `starknet_api::block::BlockInfo`: [3](#0-2) 

That `BlockInfo` is forwarded to the batcher as `ValidateBlockInput.block_info`: [4](#0-3) 

The batcher then executes every transaction in the block under that `sequencer_address`. Because both the proposer and the validator derive the `ProposalCommitment` from the same `init.builder`-derived `BlockInfo`, the `built_block == received_fin.proposal_commitment` check passes: [5](#0-4) 

The block is committed with the attacker-supplied `sequencer_address` in the block header.

On the build side, `builder` is set from a locally-configured `args.builder_address`: [6](#0-5) 

The validator has access to the same configuration but never uses it to cross-check the incoming `init.builder`.

---

### Impact Explanation

`sequencer_address` in `BlockInfo` is the address to which transaction fees are credited during execution. By setting `init.builder` to an attacker-controlled address, a Byzantine proposer causes:

1. **Fee redirection**: All sequencer fees for the block are credited to the attacker's address instead of the legitimate sequencer's address. This is a direct economic loss to the honest sequencer operator.
2. **Wrong block header**: `sequencer_address` is part of the Starknet block header and is included in the partial block hash commitment. The committed block carries a permanently wrong `sequencer_address`, corrupting the authoritative on-chain record.

Both effects are irreversible once the block is committed.

---

### Likelihood Explanation

The proposer is a consensus participant authenticated by `get_proposer_for_height`. However, in a BFT system, up to `f` nodes may be Byzantine. A single Byzantine proposer in its proposer slot can exploit this in every round it leads. No special privilege beyond being the elected proposer for a round is required. The attack requires only crafting a `ProposalInit` with a modified `builder` field — a trivial wire-level manipulation.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and check it in `is_proposal_init_valid`:

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub builder: ContractAddress,   // <-- add
    // ... existing fields
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

Populate `proposal_init_validation.builder` from the node's own configured `builder_address` (the same value used in `ProposalBuildArguments.builder_address`) when constructing `ProposalInitValidation` in `validate_proposal` inside `SequencerConsensusContext`.

---

### Proof of Concept

1. A Byzantine node is elected proposer for height H, round R.
2. It constructs `ProposalInit` with `builder = ATTACKER_ADDRESS` (any address it controls).
3. It streams the proposal to validators.
4. Each validator calls `is_proposal_init_valid` — `builder` is not checked, validation passes.
5. `initiate_validation` calls `convert_to_sn_api_block_info(init)` → `sequencer_address = ATTACKER_ADDRESS`.
6. The batcher executes all transactions with `sequencer_address = ATTACKER_ADDRESS`; fees are credited there.
7. The batcher returns a `ProposalCommitment` computed from a `PartialBlockHash` that includes `ATTACKER_ADDRESS` as `sequencer_address`.
8. The proposer's `ProposalFin.proposal_commitment` was also computed with `ATTACKER_ADDRESS`, so `built_block == received_fin.proposal_commitment` — the check passes.
9. Consensus reaches decision; `decision_reached` commits the block with `sequencer_address = ATTACKER_ADDRESS` in the block header and state.
10. All sequencer fees for block H are permanently credited to `ATTACKER_ADDRESS`.

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
