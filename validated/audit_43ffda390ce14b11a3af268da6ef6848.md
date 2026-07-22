### Title
Unvalidated `builder` field in `ProposalInit` allows any proposer to redirect all block transaction fees to an arbitrary address — (`crates/apollo_consensus_orchestrator/src/utils.rs`)

---

### Summary

`convert_to_sn_api_block_info` maps `ProposalInit.builder` directly to `BlockInfo.sequencer_address`, which is the on-chain recipient of every transaction fee in the block. `is_proposal_init_valid` never checks the `builder` field. Any validator that wins a proposer slot can therefore set `builder` to an attacker-controlled address; validators accept the proposal, execute the block with the wrong `sequencer_address`, and commit a state diff that transfers all fees to the attacker.

---

### Finding Description

`convert_to_sn_api_block_info` in `crates/apollo_consensus_orchestrator/src/utils.rs` converts a `ProposalInit` into the `starknet_api::block::BlockInfo` that the batcher uses for execution:

```rust
Ok(starknet_api::block::BlockInfo {
    block_number: init.height,
    block_timestamp: BlockTimestamp(init.timestamp),
    sequencer_address: init.builder,   // ← proposer-supplied, never validated
    ...
})
``` [1](#0-0) 

`is_proposal_init_valid` validates `timestamp`, `starknet_version`, `version_constant_commitment`, `height`, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas-price fields, and `fee_proposal_fri`. It never inspects `builder`: [2](#0-1) 

The `ProposalInit.builder` field is defined as "Address of the one who builds/sequences the block" and is freely set by the proposer from its local config: [3](#0-2) [4](#0-3) 

Because `sequencer_address` is the fee-transfer recipient in the blockifier (fees are transferred to `block_context.block_info.sequencer_address`), every transaction fee in the block flows to whatever address the proposer placed in `builder`. [5](#0-4) 

The `PartialBlockHashComponents` are computed from `block_info` (which contains the attacker-supplied `sequencer_address`), so the proposer's commitment and the validator's re-computed commitment agree — the `ProposalFinMismatch` guard does not fire: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Critical — Incorrect fee/balance with economic impact.**

Every transaction fee in the block is credited to the attacker's address instead of the legitimate sequencer. The committed state diff, block header `sequencer_address`, and all fee-token storage writes reflect the wrong recipient. The block is permanently committed with this wrong state.

---

### Likelihood Explanation

**High.** In a decentralized consensus any validator that wins a proposer slot — a normal, unprivileged role — can trigger this. No special key or admin permission is required. The attack requires only crafting a `ProposalInit` with a chosen `builder` value, which is a single field in a protobuf message the proposer already constructs and broadcasts.

---

### Recommendation

Add a `builder` check inside `is_proposal_init_valid`. The validator must compare `init_proposed.builder` against a locally-trusted value (e.g., the expected sequencer address from the committee or from a local config that mirrors the proposer's). Concretely, add `builder` to `ProposalInitValidation` and reject any proposal whose `builder` does not match:

```rust
if init_proposed.builder != proposal_init_validation.expected_builder {
    return Err(ValidateProposalError::InvalidProposalInit(...));
}
``` [8](#0-7) 

---

### Proof of Concept

1. A malicious validator wins the proposer slot for height H.
2. It constructs `ProposalInit` with `builder = ATTACKER_ADDRESS` (any address it controls).
3. It broadcasts the proposal stream to all validators.
4. Each validator calls `validate_proposal` → `is_proposal_init_valid`: all checked fields pass; `builder` is never inspected.
5. `initiate_validation` calls `convert_to_sn_api_block_info(init)` → `sequencer_address = ATTACKER_ADDRESS`.
6. The batcher executes the block; every fee transfer goes to `ATTACKER_ADDRESS`.
7. `PartialBlockHashComponents` is computed with `sequencer_address = ATTACKER_ADDRESS`; the commitment matches the proposer's `ProposalFin.proposal_commitment`.
8. `built_block == received_fin.proposal_commitment` → proposal accepted.
9. `decision_reached` commits the block; the state diff records fee-token balance increases at `ATTACKER_ADDRESS`.
10. All transaction fees for block H are permanently stolen. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-347)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let proposal_init_info = PreviousProposalInitInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L59-85)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L141-250)
```rust
pub(crate) async fn validate_proposal(
    mut args: ProposalValidateArguments,
) -> ValidateProposalResult<ProposalCommitment> {
    let mut content = Vec::new();
    let mut verify_and_store_proof_tasks: Vec<VerifyAndStoreProofTask> = Vec::new();
    let now = args.deps.clock.now();

    let Some(deadline) = now.checked_add_signed(chrono::TimeDelta::from_std(args.timeout).unwrap())
    else {
        return Err(ValidateProposalError::CannotCalculateDeadline { timeout: args.timeout, now });
    };

    is_proposal_init_valid(
        &args.proposal_init_validation,
        &args.init,
        args.deps.clock.as_ref(),
        args.deps.l1_gas_price_provider,
        &args.gas_price_params,
    )
    .await?;

    initiate_validation(
        args.deps.batcher.clone(),
        args.deps.state_sync_client,
        &args.init,
        args.proposal_id,
        args.timeout + args.batcher_timeout_margin,
        args.deps.clock.as_ref(),
        args.compare_retrospective_block_hash,
    )
    .await?;

    let deadline_params = ProposalDeadlineParams {
        clock: args.deps.clock.clone(),
        deadline,
        cancel_token: args.cancel_token.clone(),
    };

    // Validating the rest of the proposal parts.
    let (built_block, received_fin, finished_info) = loop {
        tokio::select! {
            _ = args.cancel_token.cancelled() => {
                // Ignoring batcher errors, to better reflect the proposal interruption.
                batcher_abort_proposal(args.deps.batcher.as_ref(), args.proposal_id).await.ok();
                return Err(ValidateProposalError::ProposalInterrupted(
                    "validating proposal parts".to_string(),
                ));
            }
            _ = args.deps.clock.sleep_until(deadline) => {
                // Ignoring batcher errors, to better reflect the proposal deadline timeout.
                batcher_abort_proposal(args.deps.batcher.as_ref(), args.proposal_id).await.ok();
                return Err(ValidateProposalError::ValidationTimeout(
                    "validating proposal parts".to_string(),
                ));
            }
            proposal_part = args.content_receiver.next() => {
                match handle_proposal_part(
                    args.proposal_id,
                    args.deps.batcher.as_ref(),
                    proposal_part.clone(),
                    &mut content,
                    &mut verify_and_store_proof_tasks,
                    args.deps.transaction_converter.clone(),
                    &deadline_params,
                    args.init.fee_proposal_fri,
                ).await {
                    HandledProposalPart::Finished(built_block, received_fin, finished_info) => {
                        break (built_block, received_fin, finished_info);
                    }
                    HandledProposalPart::Continue => {continue;}
                    HandledProposalPart::Invalid(err) => {
                        // No need to abort since the Batcher is the source of this info.
                        return Err(ValidateProposalError::InvalidProposal(err));
                    }
                    HandledProposalPart::Failed(fail_reason) => {
                        batcher_abort_proposal(args.deps.batcher.as_ref(), args.proposal_id).await?;
                        return Err(ValidateProposalError::ProposalPartFailed(fail_reason,proposal_part));
                    }
                    HandledProposalPart::Timeout(msg) => {
                        // Ignoring batcher errors, to better reflect the validation timeout.
                        batcher_abort_proposal(args.deps.batcher.as_ref(), args.proposal_id).await.ok();
                        return Err(ValidateProposalError::ValidationTimeout(msg));
                    }
                    HandledProposalPart::Interrupted(msg) => {
                        // Ignoring batcher errors, to better reflect the proposal interruption.
                        batcher_abort_proposal(args.deps.batcher.as_ref(), args.proposal_id).await.ok();
                        return Err(ValidateProposalError::ProposalInterrupted(msg));
                    }
                }
            }
        }
    };

    let n_executed_txs = content.iter().map(|batch| batch.len()).sum::<usize>();
    CONSENSUS_NUM_BATCHES_IN_PROPOSAL.set_lossy(content.len());
    CONSENSUS_NUM_TXS_IN_PROPOSAL.set_lossy(n_executed_txs);

    // Update valid_proposals before sending fin to avoid a race condition
    // with `repropose` being called before `valid_proposals` is updated.
    let mut valid_proposals = args.valid_proposals.lock().unwrap();
    valid_proposals.insert_proposal(args.init, content, &args.proposal_id, finished_info);

    // TODO(matan): Switch to signature validation.
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }

    Ok(built_block)
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

**File:** crates/apollo_protobuf/src/consensus.rs (L107-107)
```rust
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L174-174)
```rust
        builder: args.builder_address,
```

**File:** crates/blockifier/src/concurrency/fee_utils.rs (L40-42)
```rust
        .get_fee_token_balance(
            tx_context.block_context.block_info.sequencer_address,
            tx_context.fee_token_address()
```

**File:** crates/apollo_batcher/src/block_builder.rs (L178-179)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```
