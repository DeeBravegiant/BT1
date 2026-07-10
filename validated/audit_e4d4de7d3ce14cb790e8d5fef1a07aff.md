### Title
Unprotected `init` Function Allows Any Account to Hijack MPC Contract Initialization - (File: crates/contract/src/lib.rs)

### Summary
The `init` function of `MpcContract` lacks the `#[private]` attribute, meaning any NEAR account can call it before the legitimate deployer does. If an attacker calls `init` first — during the window between contract deployment and the deployer's own `init` call — they can set themselves as the sole participant with threshold 1, gaining complete control over the MPC signing network.

### Finding Description
The `init` function is the primary bootstrapping entry point for the MPC contract. It accepts arbitrary `ThresholdParameters` (participant set and threshold) and immediately transitions the contract into the `Running` state with those parameters. Critically, it carries no access control: it is not marked `#[private]`, so any NEAR account can call it.

```rust
// crates/contract/src/lib.rs:1924-1973
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
    ...
    let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
    Ok(Self {
        protocol_state: ProtocolContractState::Running(RunningContractState::new(...)),
        ...
        tee_state,
        accept_requests: true,
        ...
    })
}
``` [1](#0-0) 

By contrast, the sibling `init_running` function — which also initializes the contract — is correctly marked `#[private]`:

```rust
// crates/contract/src/lib.rs:1975-1979
#[private]
#[init]
#[handle_result]
pub fn init_running(...) -> Result<Self, Error> {
``` [2](#0-1) 

The `#[init]` attribute in NEAR SDK only guarantees the function runs once (panics if state already exists). It does **not** restrict who can call it. Without `#[private]`, any NEAR account is a valid caller.

The deployment documentation confirms that contract deployment and `init` are separate transactions:

```
near contract call-function as-transaction mpc-contract.test.near init file-args /tmp/init_args.json ...
``` [3](#0-2) 

This creates a window between deployment and initialization during which an attacker can call `init` first.

When `init` runs, it calls `TeeState::with_mocked_participant_attestations` for the supplied participants, which inserts mocked-valid attestations for each supplied account: [4](#0-3) [5](#0-4) 

This means an attacker who calls `init` with themselves as the sole participant also receives a mocked attestation, satisfying the `assert_caller_is_attested_participant_and_protocol_active` check used by `start_keygen_instance`, `vote_pk`, and `respond`. [6](#0-5) 

### Impact Explanation
An attacker who wins the initialization race can:
1. Set themselves as the sole participant with `threshold = 1`.
2. Call `vote_add_domains` to register signing domains.
3. Call `start_keygen_instance` and `vote_pk` to generate distributed keys under their sole control.
4. Issue threshold signatures (`respond`) for arbitrary payloads without any legitimate participant authorization.

This constitutes **unauthorized threshold signature issuance** and **complete bypass of threshold-signature requirements**, matching the Critical impact tier: *"Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization."*

### Likelihood Explanation
The deployment process separates contract deployment from `init` into distinct transactions. Any NEAR account that monitors the chain for new deployments of the MPC contract can submit a competing `init` call in the same or next block. NEAR processes transactions in order within a shard; the first `init` call to execute wins permanently. No privileged access, leaked keys, or collusion is required — only the ability to submit a NEAR transaction.

### Recommendation
Add the `#[private]` attribute to `init`, consistent with how `init_running` is already protected:

```rust
#[private]
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> {
```

Alternatively, batch the `DeployContract` and `FunctionCall(init)` actions into a single NEAR transaction so no window exists. Both mitigations should be applied together.

### Proof of Concept
1. Observe the MPC contract being deployed to `mpc-contract.near` (deployment transaction visible on-chain).
2. Before the legitimate deployer calls `init`, submit:
   ```json
   {
     "parameters": {
       "participants": {
         "next_id": 1,
         "participants": [["attacker.near", 0, {"tls_public_key": "<attacker_tls_key>"}]]
       },
       "threshold": 1
     }
   }
   ```
   to `mpc-contract.near::init` signed by `attacker.near`.
3. The contract enters `Running` state with `attacker.near` as the sole participant and threshold 1. The legitimate deployer's subsequent `init` call panics with "state already exists."
4. `attacker.near` calls `vote_add_domains` to add a Secp256k1 signing domain, then `start_keygen_instance` and `vote_pk` to generate a key under sole attacker control.
5. `attacker.near` can now call `respond` to resolve any user `sign` request with an attacker-chosen signature, or issue signatures for arbitrary foreign-chain transactions.

### Citations

**File:** crates/contract/src/lib.rs (L1924-1930)
```rust
    #[handle_result]
    #[init]
    pub fn init(
        parameters: dtos::ThresholdParameters,
        init_config: Option<dtos::InitConfig>,
    ) -> Result<Self, Error> {
        let parameters: ThresholdParameters = parameters.try_into_contract_type()?;
```

**File:** crates/contract/src/lib.rs (L1944-1945)
```rust
        let initial_participants = parameters.participants();
        let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
```

**File:** crates/contract/src/lib.rs (L1975-1979)
```rust
    // This function can be used to transfer the MPC network to a new contract.
    #[private]
    #[init]
    #[handle_result]
    pub fn init_running(
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** docs/localnet/localnet.md (L267-269)
```markdown
```shell
near contract call-function as-transaction mpc-contract.test.near init file-args /tmp/init_args.json prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' sign-as mpc-contract.test.near network-config mpc-localnet sign-with-keychain send
```
```

**File:** crates/contract/src/tee/tee_state.rs (L102-120)
```rust
impl TeeState {
    /// Creates a [`TeeState`] with an initial set of participants that will receive a valid mocked attestation.
    pub(crate) fn with_mocked_participant_attestations(participants: &Participants) -> Self {
        let mut tee_state = Self::default();

        for (account_id, _, participant_info) in participants.participants() {
            let tls_public_key = participant_info.tls_public_key.clone();
            // TODO(#1087): replace account_public_key with a real account public
            // key passed in by the caller. `Participants` does not currently
            // carry the operator's account public key, so a mocked entry
            // cannot record the real one and we use the TLS key as a unique
            // per-participant placeholder. The mock keeps the
            // participant from being kicked out of an empty `TeeState` until
            // a real `submit_participant_info` call replaces it (keyed by
            // TLS), but any caller-facing check that compares
            // `signer_account_pk` against the stored key will fail until
            // then. #1087 tracks threading real attestations through
            // initialization so this sentinel can go away.
            let node_id = NodeId {
```
