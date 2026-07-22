### Title
Unvalidated `ProposalInit.builder` Address Allows Malicious Proposer to Redirect All Block Fees and Corrupt `get_sequencer_address` Syscall Results - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`ProposalInit` carries a `builder` field (the address that builds/sequences the block). This field is passed directly as `sequencer_address` into `BlockInfo` for execution and stored in the committed block header, but `is_proposal_init_valid` never checks it against any expected value. A malicious proposer can set `init.builder` to any arbitrary address, redirecting all transaction fees for that block and corrupting the `get_sequencer_address` syscall result seen by every contract executed in that block.

### Finding Description

`ProposalInit` has two address fields:
- `proposer`: the consensus identity — validated by the consensus manager against the committee's expected proposer.
- `builder`: the address used as `sequencer_address` during block execution — **never validated**. [1](#0-0) 

`is_proposal_init_valid` checks `height`, `l1_da_mode`, `l2_gas_price_fri`, `starknet_version`, `version_constant_commitment`, timestamp bounds, all four L1 gas prices, and `fee_proposal_fri`. The `builder` field is absent from `ProposalInitValidation` and is never compared against any locally-trusted value: [2](#0-1) [3](#0-2) 

`convert_to_sn_api_block_info` then maps `init.builder` directly to `sequencer_address` in the `BlockInfo` forwarded to the batcher: [4](#0-3) 

The same address is written into the committed block header via `update_state_sync_with_new_block`: [5](#0-4) 

### Impact Explanation

**Fee theft (Critical — incorrect fee/balance effect with economic impact):** The blockifier transfers every transaction fee to `block_info.sequencer_address`. With an attacker-controlled `builder`, all fees from the block flow to the attacker's address instead of the legitimate sequencer. [6](#0-5) 

**Wrong syscall result (Critical — wrong execution result for accepted input):** Every contract that calls `get_sequencer_address` during execution of that block receives the attacker's address. Contracts that gate logic on the sequencer address (e.g., fee-token proxy patterns, access-control checks) will behave incorrectly.

**Wrong committed block header (High — authoritative-looking wrong value):** The `sequencer` field of the stored `BlockHeaderWithoutHash` is the attacker's address. This propagates into the block hash, state sync, RPC responses (`starknet_getBlockWithTxHashes`, etc.), and any downstream proof that covers the block header. [7](#0-6) 

### Likelihood Explanation

Any validator that is scheduled as the proposer for a height/round can exploit this. In a rotating BFT committee, every validator eventually proposes. The attack requires no external access — only the ability to craft a `ProposalInit` with an arbitrary `builder` field, which is a normal part of the proposal flow. The consensus manager validates `proposer` against the committee but does not touch `builder`: [8](#0-7) 

### Recommendation

Add `builder` to `ProposalInitValidation` and enforce it in `is_proposal_init_valid`. The expected value should be the node's own configured `builder_address` (already available in `ProposalBuildArguments`). Validators should reject any proposal whose `init.builder` does not match the locally-configured or committee-registered builder address for that height. [9](#0-8) 

### Proof of Concept

1. A validator is scheduled as proposer for height `H`, round `R`.
2. In `initiate_build`, it constructs `ProposalInit` with `builder: attacker_address` (any address it controls) instead of the legitimate `builder_address`.
3. The `ProposalInit` is broadcast to all peers.
4. Each peer calls `is_proposal_init_valid` — this passes because `builder` is not checked.
5. `initiate_validation` calls `convert_to_sn_api_block_info(&init)`, producing `BlockInfo { sequencer_address: attacker_address, … }`.
6. The batcher executes all transactions with `sequencer_address = attacker_address`: every fee transfer credits `attacker_address`; every `get_sequencer_address` syscall returns `attacker_address`.
7. `ProposalFin` is accepted; `decision_reached` commits the block with `sequencer = SequencerContractAddress(attacker_address)` in the block header.
8. All fees for block `H` are now in `attacker_address`; the committed block header and block hash permanently record the wrong sequencer. [10](#0-9)

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L102-107)
```rust
    /// Address of the one who proposed the block in consensus.
    pub proposer: ContractAddress,
    /// Block timestamp.
    pub timestamp: u64,
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L443-476)
```rust
async fn initiate_validation(
    batcher: Arc<dyn BatcherClient>,
    state_sync_client: SharedStateSyncClient,
    init: &ProposalInit,
    proposal_id: ProposalId,
    timeout_plus_margin: Duration,
    clock: &dyn Clock,
    compare_retrospective_block_hash: bool,
) -> ValidateProposalResult<()> {
    let chrono_timeout = chrono::Duration::from_std(timeout_plus_margin)
        .expect("Can't convert timeout to chrono::Duration");

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L397-406)
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
```

**File:** crates/blockifier/src/concurrency/fee_utils.rs (L39-62)
```rust
        let sequencer_balance = state
        .get_fee_token_balance(
            tx_context.block_context.block_info.sequencer_address,
            tx_context.fee_token_address()
        )
        // TODO(barak, 01/07/2024): Consider propagating the error.
        .unwrap_or_else(|error| {
            panic!(
                "Access to storage failed. Probably due to a bug in Papyrus. {error:?}: {error}"
            )
        });

        // Fix the transfer call info.
        fill_sequencer_balance_reads(fee_transfer_call_info, sequencer_balance);
        // Update the balance.
        add_fee_to_sequencer_balance(
            tx_context.fee_token_address(),
            state,
            tx_execution_info.receipt.fee,
            &tx_context.block_context,
            sequencer_balance,
            tx_context.tx_info.sender_address(),
            state_diff,
        );
```

**File:** crates/apollo_storage/src/header.rs (L92-93)
```rust
    /// The sequencer address that created this block.
    pub sequencer: SequencerContractAddress,
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

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L169-175)
```rust
    let init = ProposalInit {
        height: args.build_param.height,
        round: args.build_param.round,
        valid_round: args.build_param.valid_round,
        proposer: args.build_param.proposer,
        builder: args.builder_address,
        timestamp,
```
