### Title
Unvalidated `ProposalInit.builder` field allows a legitimate proposer to inject an arbitrary sequencer address into block execution context and block hash — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit` carries two identity fields: `proposer` (the consensus-layer signer, validated against the committee) and `builder` (the sequencer address used in block execution). `is_proposal_init_valid` validates every other `ProposalInit` field but never checks `builder`. Because `builder` is passed verbatim as `sequencer_address` into `BlockInfo` for both the batcher and the block-hash computation, a legitimate proposer can set it to any arbitrary address and every validator will silently accept the proposal, execute all transactions with the wrong sequencer address, and commit a block whose hash encodes that wrong address.

### Finding Description

**Unvalidated field.** `is_proposal_init_valid` in `validate_proposal.rs` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, all four L1 gas prices, and `fee_proposal_fri`. It never reads or constrains `init_proposed.builder`. [1](#0-0) 

**Unchecked field flows into execution context.** `convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is forwarded to the batcher: [2](#0-1) 

This `BlockInfo` is passed to `batcher.validate_block` via `initiate_validation`: [3](#0-2) 

**Unchecked field flows into block hash.** `BlockExecutionArtifacts::new` calls `PartialBlockHashComponents::new(&block_info, header_commitments)`, which incorporates `block_info.sequencer_address` into the partial block hash that becomes the `ProposalCommitment`: [4](#0-3) 

**Commitment check does not catch the attack.** The final guard at line 244 compares `built_block` (computed by the batcher using the attacker-supplied `builder`) against `received_fin.proposal_commitment` (also computed by the proposer using the same `builder`). Both sides use the same injected value, so they always match: [5](#0-4) 

**`ProposalInitValidation` has no `builder` field.** The struct that carries the validator's locally-trusted reference values contains `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, and `fee_actual`, but no `builder`: [6](#0-5) 

**`builder` is a free wire field.** The protobuf definition shows `builder` is field 6 in `ProposalInit`, fully controlled by the proposer: [7](#0-6) 

**Attack path:**
1. Attacker is the legitimate proposer for height H, round R (passes the `proposer != init.proposer` check in `manager.rs`).
2. Attacker sets `init.builder` to an attacker-controlled address (e.g., their own wallet, or `0x0`).
3. Every validator calls `is_proposal_init_valid` — passes, because `builder` is never read.
4. `initiate_validation` forwards `block_info` with `sequencer_address = attacker_address` to the batcher.
5. All transactions in the block execute with `get_sequencer_address()` returning `attacker_address`.
6. The partial block hash is computed over `attacker_address`.
7. `built_block == received_fin.proposal_commitment` — both encode the same injected address — so the proposal is accepted and committed.

### Impact Explanation

- **Wrong execution result (Critical):** Every contract in the block that calls `get_sequencer_address()` or `get_execution_info()` receives the attacker-supplied address instead of the legitimate sequencer address. This corrupts any contract logic that gates on the sequencer address (e.g., fee-token contracts, access-control checks, L1→L2 message handlers).
- **Wrong block hash (Critical):** The committed `PartialBlockHash` encodes the wrong `sequencer_address`, producing a block hash that diverges from what an honest node would compute for the same transactions.
- **Fee redirection (Critical):** Starknet fee collection credits the `sequencer_address`. Setting `builder` to an attacker-controlled address redirects all transaction fees in the block to the attacker.

### Likelihood Explanation

The attacker must be the legitimate proposer for a given height/round — a role that rotates among committee members. Any committee member can exploit this without any off-chain coordination. The exploit requires only crafting a `ProposalInit` with a modified `builder` field, which is a trivial wire-level change.

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`. The validator's locally-configured builder address (equivalent to `args.builder_address` on the proposer side) should be stored in the context and compared:

```rust
// In ProposalInitValidation:
pub builder: ContractAddress,

// In is_proposal_init_valid:
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

The `builder` value used to populate `ProposalInitValidation` should come from the same local configuration source used by `initiate_build` (`args.builder_address`), not from the incoming `ProposalInit`.

### Proof of Concept

1. Attacker is the scheduled proposer for block N, round 0.
2. Attacker builds a normal `ProposalInit` but sets `builder = ContractAddress::from(0xdeadbeef)`.
3. Attacker streams the proposal to all validators.
4. Each validator calls `validate_proposal` → `is_proposal_init_valid` → passes (no `builder` check).
5. `initiate_validation` sends `ValidateBlockInput { block_info: BlockInfo { sequencer_address: 0xdeadbeef, … } }` to the batcher.
6. All transactions execute with `sequencer_address = 0xdeadbeef`; `get_sequencer_address()` returns `0xdeadbeef`.
7. `PartialBlockHashComponents` encodes `0xdeadbeef`; `built_block` matches `received_fin.proposal_commitment`.
8. Consensus commits the block. The on-chain state records `sequencer_address = 0xdeadbeef` for block N. [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L455-475)
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
    Ok(())
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

**File:** crates/apollo_batcher/src/block_builder.rs (L178-194)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            #[cfg(feature = "os_input")]
            initial_reads,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L47-64)
```text
message ProposalInit {
    uint64 height                     = 1;
    uint32 round                      = 2;
    optional uint32 valid_round       = 3;
    Address proposer                  = 4;
    uint64 timestamp                  = 5;
    Address builder                   = 6;
    L1DataAvailabilityMode l1_da_mode = 7;
    Uint128 l2_gas_price_fri          = 8;
    Uint128 l1_gas_price_fri          = 9;
    Uint128 l1_data_gas_price_fri     = 10;
    Uint128 l1_gas_price_wei          = 11;
    Uint128 l1_data_gas_price_wei     = 12;
    string starknet_version           = 13;
    Hash version_constant_commitment   = 14;
    // Proposer's recommended fee for future blocks. Present iff Starknet version >= V0_14_3.
    optional Uint128 fee_proposal_fri = 15;
}
```

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
