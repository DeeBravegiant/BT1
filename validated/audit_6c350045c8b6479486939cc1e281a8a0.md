### Title
Unprotected `init` Function Allows Any Caller to Seize Full MPC Participant Control Before Legitimate Initialization — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `MpcContract::init` function carries the NEAR SDK `#[init]` attribute but **no `#[private]` guard**. Any external account can call it in the block window between contract deployment and the deployer's own `init` transaction. A successful front-run installs the attacker as the sole participant (threshold 1-of-1), giving them permanent, irrevocable control over the MPC signing network and the ability to issue unauthorized threshold signatures for every subsequent user request.

---

### Finding Description

`MpcContract::init` is the primary initialization entry point for the on-chain MPC contract:

```rust
// crates/contract/src/lib.rs  line 1924-1929
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
``` [1](#0-0) 

The NEAR SDK `#[init]` attribute only enforces **single-call semantics** (panics if state already exists). It places **no restriction on the caller's identity**. There is no `#[private]` attribute, no `assert_caller_is_signer` check, and no owner/deployer verification inside the function body. [2](#0-1) 

Contrast this with `init_running`, the other initialization path, which is correctly guarded:

```rust
// crates/contract/src/lib.rs  line 1976-1979
#[private]
#[init]
#[handle_result]
pub fn init_running(...)
``` [3](#0-2) 

`#[private]` in NEAR SDK enforces `predecessor_account_id == current_account_id`, meaning only the contract itself can call `init_running`. `init` has no equivalent protection.

The deployment workflow, as documented and scripted, performs deployment and initialization as **two separate transactions**:

```bash
# scripts/launch-localnet.sh line 216
near contract call-function as-transaction mpc-contract.test.near init \
  file-args ${init_args} ... sign-as mpc-contract.test.near ...
``` [4](#0-3) 

This creates a block-level window between the `DeployContract` action landing on-chain and the deployer's `init` transaction being included. During this window, any account can submit its own `init` call.

Inside `init`, the attacker-supplied `parameters` are accepted without any caller check, and `TeeState::with_mocked_participant_attestations` is called with the attacker's participant set, granting them pre-approved (mocked-valid) TEE attestation status:

```rust
// crates/contract/src/lib.rs  line 1944-1945
let initial_participants = parameters.participants();
let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
``` [5](#0-4) 

The contract is then set to `ProtocolContractState::Running` with the attacker as the sole participant and `accept_requests: true`. [6](#0-5) 

Because `#[init]` prevents any subsequent call to `init`, the legitimate deployer cannot recover the contract at the same address. The attacker's participant set is permanently installed.

---

### Impact Explanation

**Critical — Unauthorized threshold signature issuance.**

After a successful front-run:

1. The attacker is the only participant (threshold 1-of-1) with mocked-valid TEE attestation.
2. The attacker calls `vote_add_domains` to add signing domains; as the sole voter they immediately reach threshold.
3. The attacker's node performs key generation alone, producing keys entirely under their control.
4. Every `sign()`, `request_app_private_key()`, and `verify_foreign_transaction()` request submitted by legitimate users is answered by the attacker's node via `respond` / `respond_ckd` / `respond_verify_foreign_tx`.
5. The attacker issues signatures over arbitrary payloads, enabling forgery of cross-chain transactions, unauthorized bridge withdrawals, and fraudulent CKD outputs.
6. No legitimate participant can ever reclaim the contract at that address; the only recovery is redeployment at a new address, which requires all integrators to migrate.

This matches: **Critical — Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.**

---

### Likelihood Explanation

**Medium-High.** The attack requires:

- Monitoring the NEAR blockchain for a `DeployContract` action targeting the MPC contract account (trivially done with any NEAR indexer).
- Submitting an `init` transaction in the same or next block before the deployer's `init` lands.

NEAR block times are ~1 second. The deployment scripts issue `init` as a separate CLI invocation seconds after deployment. An attacker running an indexer can detect the deployment and race the `init` call. No privileged access, no key material, and no collusion is required — only a funded NEAR account.

---

### Recommendation

Add `#[private]` to `init`, mirroring the protection already applied to `init_running`:

```rust
#[private]
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
```

`#[private]` enforces `predecessor_account_id == current_account_id`, so only the contract account itself (i.e., a batch action in the same deployment transaction, or a self-call) can invoke `init`. This eliminates the front-run window entirely.

Alternatively, combine `DeployContract` and the `init` function call into a single atomic batch transaction at deployment time, so no inter-block window exists.

---

### Proof of Concept

1. Operator deploys `mpc_contract.wasm` to `mpc-contract.near` (block N). State is empty; `#[init]` has not yet been called.
2. Attacker observes the `DeployContract` receipt in block N via an indexer.
3. Attacker submits in block N or N+1:
   ```json
   near contract call-function as-transaction mpc-contract.near init \
     json-args '{
       "parameters": {
         "participants": {
           "next_id": 1,
           "participants": [["attacker.near", 0, {"tls_public_key": "<attacker_p2p_key>", "url": "https://attacker.example"}]]
         },
         "threshold": 1
       }
     }' \
     prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' \
     sign-as attacker.near network-config mainnet sign-with-keychain send
   ```
4. The call succeeds: `ProtocolContractState::Running` is set with `attacker.near` as sole participant, threshold 1, `accept_requests: true`, and mocked-valid TEE attestation.
5. Operator's subsequent `init` call panics: `"Cannot initialize contract that is already initialized"`.
6. Attacker calls `vote_add_domains` (reaches threshold immediately), runs their node to generate keys, and begins responding to all user `sign()` requests with attacker-controlled signatures.
7. All cross-chain transactions signed via this contract are under attacker control.

### Citations

**File:** crates/contract/src/lib.rs (L1924-1929)
```rust
    #[handle_result]
    #[init]
    pub fn init(
        parameters: dtos::ThresholdParameters,
        init_config: Option<dtos::InitConfig>,
    ) -> Result<Self, Error> {
```

**File:** crates/contract/src/lib.rs (L1940-1973)
```rust
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

**File:** crates/contract/src/lib.rs (L1976-1979)
```rust
    #[private]
    #[init]
    #[handle_result]
    pub fn init_running(
```

**File:** scripts/launch-localnet.sh (L216-216)
```shellscript
  run_quiet_on_success "near contract call-function as-transaction mpc-contract.test.near init file-args ${init_args} prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' sign-as mpc-contract.test.near network-config mpc-localnet sign-with-keychain send"
```
