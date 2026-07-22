### Title
Unvalidated `builder` Field in `ProposalInit` Allows Any Proposer to Redirect Block Fees and Corrupt the Sequencer Address in Committed Blocks — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`ProposalInit.builder` is the proposer-supplied sequencer address that is passed verbatim into `BlockInfo.sequencer_address` for every block execution. `is_proposal_init_valid` validates every other economically-sensitive field in `ProposalInit` (height, L1/L2 gas prices, DA mode, starknet version, version constant commitment, fee proposal) but never checks `builder`. Any validator that wins a proposer slot can set `builder` to an arbitrary address; all validators will accept the proposal, execute the block with the attacker-controlled sequencer address, and commit a block whose header contains the wrong sequencer address and whose fee accounting credits the wrong recipient.

---

### Finding Description

`ProposalInit` carries a `builder` field:

```rust
/// Address of the one who builds/sequences the block.
pub builder: ContractAddress,
``` [1](#0-0) 

During proposal building, `builder` is set from the local node's configured `builder_address`:

```rust
builder: args.builder_address,
``` [2](#0-1) 

This value is serialised into the protobuf wire message and sent to all validators. On the validator side, `convert_to_sn_api_block_info` maps `init.builder` directly to `sequencer_address` in the `BlockInfo` that is handed to the batcher for execution:

```rust
sequencer_address: init.builder,
``` [3](#0-2) 

`is_proposal_init_valid` performs extensive checks on every other field — height, timestamp window, starknet version, version constant commitment, L1/L2 gas prices (FRI and WEI), DA mode, and fee proposal — but contains **no check on `builder`**: [4](#0-3) 

The `ProposalInitValidation` struct, which carries all locally-trusted reference values used by `is_proposal_init_valid`, has no `builder` field at all:

```rust
pub(crate) struct ProposalInitValidation {
    pub height: BlockNumber,
    pub block_timestamp_window_seconds: u64,
    pub previous_proposal_init: Option<PreviousProposalInitInfo>,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub l2_gas_price_fri: GasPrice,
    pub starknet_version: StarknetVersion,
    pub fee_actual: Option<GasPrice>,
}
``` [5](#0-4) 

The `builder_address` is a per-node static config parameter: [6](#0-5) 

Because the validator never checks `init.builder` against any expected value, a malicious proposer can set it to any `ContractAddress` and every honest validator will accept the proposal, execute the block with the attacker-supplied sequencer address, and commit the result.

---

### Impact Explanation

`sequencer_address` in `BlockInfo` is the address to which transaction fees are credited during execution. By setting `builder` to an attacker-controlled address, the malicious proposer causes:

1. **Fee redirection**: All transaction fees in the block are credited to the attacker's address instead of the legitimate sequencer. This is a direct, per-block economic loss for the legitimate sequencer and gain for the attacker.

2. **Wrong block header committed to state**: The committed block header contains the attacker-supplied sequencer address. Because both the proposer's batcher and every validator's batcher receive the same `builder` value via `initiate_validation → convert_to_sn_api_block_info`, both sides compute the same (wrong) block hash, so the `ProposalFin` commitment check passes and the corrupted block is finalised. [7](#0-6) 

The corrupted sequencer address is also stored in the committed block header, making it visible to RPC clients and downstream provers.

---

### Likelihood Explanation

Any validator that is elected proposer for a round can exploit this. No special privilege beyond being in the active validator set is required. The attack is silent — it produces no validation error, no log warning, and no metric increment. It can be repeated every round the attacker is proposer.

---

### Recommendation

Add `builder` to `ProposalInitValidation` and check it in `is_proposal_init_valid`. Each validator node already has a locally-configured `builder_address`; the validator should reject any `ProposalInit` whose `builder` field does not match the network-agreed sequencer address. If the sequencer address is expected to vary per proposer, the network must establish a canonical mapping (e.g., from the validator set) and enforce it here.

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

---

### Proof of Concept

1. Attacker controls validator node `V_attacker` with `builder_address = 0xLEGIT` in config.
2. When `V_attacker` wins a proposer slot, it overrides `builder` in the constructed `ProposalInit` to `0xATTACKER` before sending.
3. All honest validators receive the `ProposalInit` and call `validate_proposal` → `is_proposal_init_valid`. None of the checks touch `builder`; the function returns `Ok(())`.
4. `initiate_validation` calls `convert_to_sn_api_block_info(&init)`, which sets `sequencer_address = 0xATTACKER` in the `BlockInfo` passed to the batcher.
5. The batcher executes all transactions with `sequencer_address = 0xATTACKER`; all fees are credited to `0xATTACKER`.
6. The batcher returns a `partial_block_hash` that encodes `0xATTACKER` as the sequencer. The proposer's batcher computed the same hash (it also used `0xATTACKER`), so `built_block == received_fin.proposal_commitment` and the proposal is accepted.
7. The block is committed with `sequencer_address = 0xATTACKER` in the block header and all fee balances updated accordingly. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_protobuf/src/consensus.rs (L106-107)
```rust
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L174-174)
```rust
        builder: args.builder_address,
```

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L141-249)
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
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L252-418)
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
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L210-215)
```rust
            ser_param(
                "builder_address",
                &self.builder_address,
                "The address of the contract that builds the block.",
                ParamPrivacyInput::Public,
            ),
```
