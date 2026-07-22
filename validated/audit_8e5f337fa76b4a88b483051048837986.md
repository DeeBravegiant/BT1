### Title
Unvalidated Zero `builder_address` in `ContextStaticConfig` Propagates as Zero Sequencer Address into Block Header Commitments and `get_sequencer_address` Syscall Results — (File: `crates/apollo_consensus_orchestrator_config/src/config.rs`)

---

### Summary

The `builder_address` field in `ContextStaticConfig` defaults to `ContractAddress::default()` (= `0x0`) with no zero-address validation at any layer. This value flows directly into `ProposalInit.builder`, which is committed as the `sequencer` field in `BlockHeaderWithoutHash`. Any sequencer node deployed with the default configuration will produce block headers with a zero sequencer address, causing the `get_sequencer_address` syscall to return `0x0` for all blocks and corrupting the block hash commitment. The proposal-init validator (`is_proposal_init_valid`) does not check the `builder` field, so a malicious proposer can also deliberately set it to `0x0` and have the proposal accepted by all validators.

---

### Finding Description

**Root cause — default and schema both set `builder_address` to `0x0`:**

`ContextStaticConfig::default()` explicitly sets `builder_address = ContractAddress::default()`, which is the zero address: [1](#0-0) 

The canonical config schema ships the same zero default: [2](#0-1) 

The struct derives `Validate` but carries no `#[validate(...)]` attribute on `builder_address`, so the validator framework never rejects a zero value: [3](#0-2) 

**Propagation — zero flows into `ProposalInit.builder` unchecked:**

`initiate_build()` copies `args.builder_address` directly into `ProposalInit.builder` with no guard: [4](#0-3) 

The `builder_address` is sourced from `self.config.static_config.builder_address` without any non-zero assertion: [5](#0-4) 

**Propagation — zero `builder` becomes the committed sequencer address:**

`update_state_sync_with_new_block()` converts `init.builder` directly into `SequencerContractAddress` and writes it into `BlockHeaderWithoutHash.sequencer`: [6](#0-5) 

**No guard in proposal-init validation:**

`is_proposal_init_valid()` validates timestamp, starknet version, `version_constant_commitment`, height, `l1_da_mode`, `l2_gas_price_fri`, all four L1 gas prices, and `fee_proposal_fri` — but never checks `init_proposed.builder`: [7](#0-6) 

Because the validator does not check `builder`, a malicious proposer can also deliberately set `ProposalInit.builder = 0x0` and every honest validator will accept the proposal.

---

### Impact Explanation

**Wrong block header commitment (Critical — wrong state/storage value):**
`BlockHeaderWithoutHash.sequencer = SequencerContractAddress(0x0)` is committed to state sync and used in the block hash computation. Every block produced by a default-configured node, or by a malicious proposer, carries a zero sequencer address in its header.

**Wrong `get_sequencer_address` syscall result (Critical — wrong syscall/execution result):**
The Starknet OS exposes the sequencer address from `BlockInfo` to contracts via the `get_sequencer_address` syscall. With `sequencer = 0x0`, any contract that calls `get_sequencer_address` receives `0x0` instead of the real sequencer address. Contracts that use this for access control (e.g., fee-token contracts, sequencer-gated logic) will behave incorrectly for every transaction in every affected block.

---

### Likelihood Explanation

**Misconfiguration path (Medium):** The default value in both `ContextStaticConfig::default()` and `config_schema.json` is `"0x0"`. Any operator who deploys the sequencer without explicitly overriding `builder_address` will silently produce blocks with a zero sequencer address. There is no startup assertion, no `Validate` rule, and no log warning.

**Malicious-proposer path (Low-Medium):** A consensus participant acting as proposer can set `ProposalInit.builder = 0x0` in any round. Because `is_proposal_init_valid` does not check `builder`, all honest validators accept the proposal, and the corrupted sequencer address is committed to the canonical chain.

---

### Recommendation

1. Add a non-zero validation for `builder_address` in `ContextStaticConfig`, either via a `#[validate(custom = "validate_non_zero_address")]` attribute or an explicit check in a `validate_static_config` schema function.
2. Add a non-zero check for `init_proposed.builder` inside `is_proposal_init_valid()` so that a malicious proposer cannot inject a zero (or otherwise invalid) sequencer address into the block header.
3. Change the default value in `ContextStaticConfig::default()` and `config_schema.json` from `"0x0"` to a sentinel that forces operators to supply a real address (e.g., make the field `Option<ContractAddress>` and require `Some` at startup).

---

### Proof of Concept

1. Start a sequencer node using the default `config_schema.json` without overriding `consensus_manager_config.context_config.static_config.builder_address` (default `"0x0"`).
2. The node calls `initiate_build()`, which constructs `ProposalInit { builder: ContractAddress(0x0), … }`.
3. The proposal is broadcast; validators call `is_proposal_init_valid()`, which does not check `builder`, and accept the proposal.
4. `update_state_sync_with_new_block()` writes `BlockHeaderWithoutHash { sequencer: SequencerContractAddress(ContractAddress(0x0)), … }` to state sync.
5. Any contract executing in that block that calls `get_sequencer_address` receives `0x0`.
6. Alternatively: a malicious proposer explicitly sets `ProposalInit.builder = ContractAddress(0x0)` in a crafted proposal; validators accept it for the same reason; the zero sequencer address is committed to the canonical chain.

### Citations

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L154-165)
```rust
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Validate)]
pub struct ContextStaticConfig {
    /// Buffer size for streaming outbound proposals.
    pub proposal_buffer_size: usize,
    /// The chain id of the Starknet chain.
    pub chain_id: ChainId,
    /// Maximum allowed deviation (seconds) of a proposed block's timestamp from the current time.
    pub block_timestamp_window_seconds: u64,
    /// The data availability mode, true: Blob, false: Calldata.
    pub l1_da_mode: bool,
    /// The address of the contract that builds the block.
    pub builder_address: ContractAddress,
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L248-261)
```rust
impl Default for ContextStaticConfig {
    fn default() -> Self {
        Self {
            proposal_buffer_size: 100,
            chain_id: ChainId::Mainnet,
            block_timestamp_window_seconds: 1,
            l1_da_mode: true,
            builder_address: ContractAddress::default(),
            validate_proposal_margin_millis: Duration::from_millis(10_000),
            build_proposal_time_ratio_for_retrospective_block_hash: 0.7,
            retrospective_block_hash_retry_interval_millis: Duration::from_millis(500),
            behavior_mode: BehaviorMode::default(),
        }
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L2797-2801)
```json
  "consensus_manager_config.context_config.static_config.builder_address": {
    "description": "The address of the contract that builds the block.",
    "privacy": "Public",
    "value": "0x0"
  },
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L397-412)
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L253-320)
```rust
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
```
