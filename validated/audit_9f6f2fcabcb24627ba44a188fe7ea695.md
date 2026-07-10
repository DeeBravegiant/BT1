### Title
Unprotected `init` Function Allows Any Caller to Hijack MPC Network Initialization — (`File: crates/contract/src/lib.rs`)

### Summary
The `MpcContract::init` function lacks the `#[private]` access-control attribute present on every other sensitive initializer in the same file. Because contract deployment and initialization are performed as separate transactions in the production deployment scripts, any unprivileged NEAR account can race to call `init` first, supplying attacker-controlled participants and threshold parameters, and thereby seizing full governance of the MPC signing network.

### Finding Description

The contract exposes two `#[init]`-decorated constructors:

1. `init` — **no** `#[private]` guard
2. `init_running` — protected with `#[private]`
3. `migrate` — protected with `#[private]`

```rust
// Contract developer helper API
#[near]
impl MpcContract {
    #[handle_result]
    #[init]                          // ← no #[private]
    pub fn init(
        parameters: dtos::ThresholdParameters,
        init_config: Option<dtos::InitConfig>,
    ) -> Result<Self, Error> {
``` [1](#0-0) 

Compare with the protected sibling:

```rust
    #[private]   // ← present here
    #[init]
    #[handle_result]
    pub fn init_running( ...
``` [2](#0-1) 

In NEAR SDK, `#[init]` only prevents a second call once state exists; it does **not** restrict the caller identity. `#[private]` is the attribute that enforces `predecessor_account_id == current_account_id`, i.e., only the contract account itself may invoke the function.

The production deployment scripts explicitly separate deployment from initialization into two distinct transactions:

```bash
# Step 1 – deploy without init
near contract deploy "$MPC_CONTRACT_ACCOUNT" use-file "$MPC_CONTRACT_PATH" \
    without-init-call ...

# Step 2 – init (separate transaction, separate block)
near contract call-function as-transaction "$MPC_CONTRACT_ACCOUNT" init \
    file-args "$INIT_ARGS_JSON" ...
``` [3](#0-2) 

This creates a window — potentially spanning multiple blocks — during which any external account can submit a call to `init` with arbitrary `parameters` (participant set, threshold) and `init_config`.

### Impact Explanation

`init` writes the entire governance state of the MPC network:

- **Participant set** — who is authorized to vote, reshare keys, and call `respond`
- **Threshold** — how many participants are required for governance and signing
- **Config** — operational parameters such as gas limits and TEE upgrade deadlines [4](#0-3) 

An attacker who calls `init` first with a single-participant set (themselves, threshold = 1) gains:

- Exclusive ability to call `vote_add_domains`, `vote_new_parameters`, `vote_pk`, `vote_reshared`, and `respond`
- The ability to complete key generation (`vote_pk`) and produce threshold signatures (`respond`) unilaterally
- Full control over which foreign-chain transactions are signed and delivered

This maps directly to the allowed critical impact: **unauthorized threshold signature issuance and confidential key derivation without required participant authorization**, and **bypass of threshold-signature requirements**.

### Likelihood Explanation

- The deployment scripts in the repository (`deploy-tee-cluster.sh`, testnet setup guide, localnet guide) all perform deployment and initialization as separate steps with no atomicity guarantee.
- An attacker monitoring the NEAR chain for `DeployContract` actions targeting the known MPC contract account can submit the `init` call in the very next block.
- No special privilege, key material, or collusion is required — only a funded NEAR account and knowledge of the contract address.
- The attack is silent: the deployer's subsequent `init` call will simply panic ("state already exists"), which may be misread as a benign deployment error, leaving the attacker-controlled state in place. [5](#0-4) 

### Recommendation

Add `#[private]` to `init`, consistent with `init_running` and `migrate`:

```rust
#[handle_result]
#[init]
#[private]   // ← add this
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
```

`#[private]` causes the NEAR SDK to assert `predecessor_account_id == current_account_id` at runtime, so only the contract account itself (signing the transaction directly) can call `init`. This matches the pattern already applied to `init_running` and `migrate`. [6](#0-5) 

### Proof of Concept

1. Operator deploys `mpc_contract.wasm` to `mpc-contract.near` with `without-init-call`.
2. Attacker observes the `DeployContract` receipt on-chain.
3. Attacker submits, from `attacker.near`:
   ```json
   near contract call-function as-transaction mpc-contract.near init \
     json-args '{
       "parameters": {
         "threshold": 1,
         "participants": {
           "next_id": 1,
           "participants": [["attacker.near", 0, {"tls_public_key": "<attacker_key>", "url": "https://attacker.example"}]]
         }
       }
     }' prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' \
     sign-as attacker.near network-config mainnet sign-with-keychain send
   ```
4. The call succeeds; contract state is now initialized with `attacker.near` as the sole participant at threshold 1.
5. The legitimate operator's `init` call panics: contract state already exists.
6. Attacker calls `vote_add_domains` to add a signing domain, then `vote_pk` to complete key generation, then `respond` to issue signatures — all unilaterally, with no other participant required. [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L1921-1973)
```rust
// Contract developer helper API
#[near]
impl MpcContract {
    #[handle_result]
    #[init]
    pub fn init(
        parameters: dtos::ThresholdParameters,
        init_config: Option<dtos::InitConfig>,
    ) -> Result<Self, Error> {
        let parameters: ThresholdParameters = parameters.try_into_contract_type()?;
        // Log participant count and hash - full parameters exceed NEAR's 16KB log limit at ~100 participants
        let params_hash = env::sha256_array(borsh::to_vec(&parameters).unwrap());
        log!(
            "init: signer={}, num_participants={}, parameters_hash={:?}, init_config={:?}",
            env::signer_account_id(),
            parameters.participants().len(),
            params_hash,
            init_config,
        );
        parameters.validate()?;

        // TODO(#1087): Every participant must have a valid attestation, otherwise we risk
        // participants being immediately kicked out once contract transitions into running.
        let initial_participants = parameters.participants();
        let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);

        Ok(Self {
            protocol_state: ProtocolContractState::Running(RunningContractState::new(
                DomainRegistry::default(),
                Keyset::new(EpochId::new(0), Vec::new()),
                parameters,
                AddDomainsVotes::default(),
            )),
            pending_signature_requests: LookupMap::new(StorageKey::PendingSignatureRequestsV4),
            pending_ckd_requests: LookupMap::new(StorageKey::PendingCKDRequestsV3),
            pending_verify_foreign_tx_requests: LookupMap::new(
                StorageKey::PendingVerifyForeignTxRequestsV2,
            ),
            proposed_updates: ProposedUpdates::default(),
            config: init_config.map(Into::into).unwrap_or_default(),
            tee_state,
            accept_requests: true,
            node_migrations: NodeMigrations::default(),
            metrics: Default::default(),
            node_foreign_chain_support: Default::default(),
            foreign_chains: Lazy::new(
                StorageKey::ForeignChainMetadata,
                ForeignChainsMetadata::default(),
            ),
            tee_verifier_account_id: None,
            tee_verifier_votes: TeeVerifierVotes::default(),
        })
    }
```

**File:** crates/contract/src/lib.rs (L1975-1980)
```rust
    // This function can be used to transfer the MPC network to a new contract.
    #[private]
    #[init]
    #[handle_result]
    pub fn init_running(
        domains: Vec<DomainConfig>,
```

**File:** localnet/tee/scripts/rust-launcher/deploy-tee-cluster.sh (L1141-1183)
```shellscript
  # FIX #5: retry wrapper + sleep
  near_tx_retry "deploy contract to $MPC_CONTRACT_ACCOUNT" \
     near contract deploy "$MPC_CONTRACT_ACCOUNT" use-file "$MPC_CONTRACT_PATH" \
      without-init-call network-config "$NEAR_NETWORK_CONFIG" sign-with-keychain send
  near_sleep "deploy contract"
}

add_node_keys_from_file() {
  local keys_file="$1"
  log "Adding node keys to NEAR accounts using $keys_file"
  [ -f "$keys_file" ] || { err "Missing keys file at $keys_file. Run collect phase first."; exit 1; }

  jq -c '.[]' "$keys_file" | while read -r row; do
    local acct signer responder
    acct="$(echo "$row" | jq -r .account)"
    signer="$(echo "$row" | jq -r .signer_pk)"
    responder="$(echo "$row" | jq -r .responder_pk)"

    log "$acct: add signer key"
    near_add_key_skip_if_exists "$acct" "$signer" "signer"

    log "$acct: add responder key"
    near_add_key_skip_if_exists "$acct" "$responder" "responder"
  done
}

add_node_keys_from_keysjson() {
  add_node_keys_from_file "$KEYS_JSON"
}

init_contract() {
  log "Initializing contract using $INIT_ARGS_JSON"
  [ -f "$INIT_ARGS_JSON" ] || { err "Missing init_args.json at $INIT_ARGS_JSON. Run init_args phase first."; exit 1; }

  # FIX #5: retry wrapper + sleep
  near_tx_retry "init contract $MPC_CONTRACT_ACCOUNT" \
     near contract call-function as-transaction "$MPC_CONTRACT_ACCOUNT" init \
      file-args "$INIT_ARGS_JSON" prepaid-gas '300.0 Tgas' \
      attached-deposit '0 NEAR' sign-as "$MPC_CONTRACT_ACCOUNT" \
      network-config "$NEAR_NETWORK_CONFIG" sign-with-keychain send

  near_sleep "init contract"
}
```
