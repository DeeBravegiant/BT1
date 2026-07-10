### Title
Unguarded `init` Allows Any Caller to Hijack MPC Participant Set on Fresh Deployment - (File: `crates/contract/src/lib.rs`)

### Summary
The `MpcContract::init` function lacks the `#[private]` access-control attribute present on every other privileged initializer (`init_running`, `migrate`). Any NEAR account can call `init` on a freshly deployed but uninitialized contract, supplying an arbitrary participant set and threshold, before the legitimate deployer does. This is the direct NEAR analog of the Solady `_guardInitializeOwner` omission: both allow an unprivileged caller to seize control of the contract's root-of-trust state during the deployment window.

---

### Finding Description

`MpcContract::init` is decorated with `#[init]` (which only prevents a *second* call once state exists) but carries no `#[private]` guard:

```rust
// crates/contract/src/lib.rs  lines 1924-1929
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
``` [1](#0-0) 

Compare with the two other initializers, which are both `#[private]`:

```rust
// init_running – line 1976
#[private]
#[init]
pub fn init_running(...) -> Result<Self, Error> { ... }

// migrate – line 2060
#[private]
#[init(ignore_state)]
pub fn migrate() -> Result<Self, Error> { ... }
``` [2](#0-1) [3](#0-2) 

In NEAR SDK, `#[private]` asserts `predecessor_account_id == current_account_id`, blocking any external caller. Without it, `init` is callable by any NEAR account as long as the contract state does not yet exist.

The deployment workflow—documented in the README and all deployment scripts—deploys the WASM in one transaction and calls `init` in a separate, subsequent transaction:

```bash
# deploy (separate tx)
near deploy mpc-contract.test.near mpc_contract.wasm
# init (separate tx — window exists here)
near contract call-function as-transaction mpc-contract.test.near init \
  file-args /tmp/init_args.json ...
``` [4](#0-3) [5](#0-4) 

Between these two transactions there is a block-level window in which the contract has code but no state. Any account that calls `init` first wins: the `#[init]` guard then prevents the legitimate deployer's subsequent call from succeeding.

The contract's own test suite confirms that `init_running` is private and rejects external callers, but no equivalent test exists for `init`:

```rust
// crates/contract/tests/sandbox/upgrade_to_current_contract.rs  lines 438-474
#[tokio::test]
async fn init_running_rejects_external_callers_pre_initialization() { ... }
// No analogous test for `init`
``` [6](#0-5) 

---

### Impact Explanation

`init` writes the entire root-of-trust state of the MPC network: the participant set, governance threshold, and TEE state. An attacker who calls it first can:

1. Install themselves as the sole participant (threshold = 1-of-1).
2. Call `vote_add_domains` to register signing domains.
3. Drive key generation unilaterally (they meet the threshold alone).
4. Issue threshold signatures for any foreign-chain transaction without any legitimate participant's involvement.

This satisfies the **Critical** impact class: *Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization*, and *Bypass of threshold-signature requirements or unauthorized access to MPC key shares*.

The legitimate deployer cannot recover by calling `init` again—`#[init]` blocks it. Recovery requires deleting and redeploying the contract account, during which the attacker's fraudulent key material may already have been used.

---

### Likelihood Explanation

- NEAR block time is ~1 second. The window between `deploy` and `init` is at least one block.
- NEAR transactions are publicly visible in the mempool and on-chain. An attacker running a NEAR node or watching a block explorer can detect the deployment and submit a competing `init` call in the next block.
- No special privilege, key material, or collusion is required—only a funded NEAR account and knowledge of the contract's ABI (which is public).
- The attack is fully deterministic and requires no brute-force or probabilistic success.

---

### Recommendation

Add `#[private]` to `init`, matching the pattern already used by `init_running` and `migrate`:

```rust
#[private]
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> { ... }
```

The deployer must then initialize via a NEAR batch transaction that atomically deploys the WASM and calls `init` from the contract account itself (so `predecessor == current_account_id`), eliminating the race window entirely. This is the same pattern already used by the upgrade path in `ProposedUpdates::do_update`:

```rust
promise = promise.deploy_contract(code).function_call(
    method_names::MIGRATE, Vec::new(), NearToken::from_near(0), gas,
);
``` [7](#0-6) 

---

### Proof of Concept

1. Deploy the MPC contract WASM to a fresh account `mpc-contract.near` (no state yet).
2. Before the legitimate operator calls `init`, the attacker submits:
   ```bash
   near contract call-function as-transaction mpc-contract.near init \
     json-args '{
       "parameters": {
         "threshold": 1,
         "participants": {
           "next_id": 1,
           "participants": [["attacker.near", 0, {"tls_public_key": "...", "url": "..."}]]
         }
       }
     }' \
     sign-as attacker.near network-config mainnet sign-with-keychain send
   ```
3. The call succeeds: `MpcContract` is now initialized with `attacker.near` as the sole participant at threshold 1-of-1.
4. The legitimate operator's `init` call fails: `"Contract already initialized"`.
5. The attacker calls `vote_add_domains` to register a `Secp256k1` signing domain, drives DKG alone, and can now call `sign()` to obtain threshold signatures for arbitrary foreign-chain transactions—without any legitimate MPC participant's involvement. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1924-1973)
```rust
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

**File:** crates/contract/src/lib.rs (L1976-1978)
```rust
    #[private]
    #[init]
    #[handle_result]
```

**File:** crates/contract/src/lib.rs (L2060-2063)
```rust
    #[private]
    #[init(ignore_state)]
    #[handle_result]
    pub fn migrate() -> Result<Self, Error> {
```

**File:** docs/localnet/localnet.md (L265-269)
```markdown
Now, we should be ready to call the `init` function on the contract.

```shell
near contract call-function as-transaction mpc-contract.test.near init file-args /tmp/init_args.json prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' sign-as mpc-contract.test.near network-config mpc-localnet sign-with-keychain send
```
```

**File:** scripts/launch-localnet.sh (L215-217)
```shellscript
  echo "Initializing contract"
  run_quiet_on_success "near contract call-function as-transaction mpc-contract.test.near init file-args ${init_args} prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' sign-as mpc-contract.test.near network-config mpc-localnet sign-with-keychain send"
  run_quiet_on_success_with_retries "near contract call-function as-read-only mpc-contract.test.near state json-args {} network-config mpc-localnet now"
```

**File:** crates/contract/tests/sandbox/upgrade_to_current_contract.rs (L438-474)
```rust
async fn init_running_rejects_external_callers_pre_initialization() {
    let (worker, contract) = init().await;
    let number_of_participants = 2;
    let (accounts, participants) = gen_accounts(&worker, number_of_participants).await;

    let threshold_parameters = ThresholdParameters::new(
        participants.clone(),
        Threshold::new(number_of_participants as u64),
    )
    .unwrap();

    let init_running_args = serde_json::json!({
            "domains": [],
            "next_domain_id": 0,
            "keyset": Keyset::new(EpochId::new(2), vec![]),
            "parameters": threshold_parameters,
    });

    let execution_error = accounts[0]
        .call(contract.id(), method_names::INIT_RUNNING)
        .max_gas()
        .args_json(init_running_args)
        .transact()
        .await
        .unwrap()
        .into_result()
        .expect_err("method is private and not callable from participant account.");

    let error_message = format!("{:?}", execution_error);

    let expected_error_message = "Smart contract panicked: Method init_running is private";

    assert!(
        error_message.contains(expected_error_message),
        "init_running call was accepted by external caller. expected method to be private. {:?}",
        error_message
    )
```

**File:** crates/contract/src/update.rs (L204-211)
```rust
            Update::Contract(code) => {
                // deploy contract then do a `migrate` call to migrate state.
                promise = promise.deploy_contract(code).function_call(
                    method_names::MIGRATE,
                    Vec::new(),
                    NearToken::from_near(0),
                    gas,
                );
```
