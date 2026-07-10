### Title
`accept_requests` Initialized to `true` by Default, Bypassing TEE Validation Gate Before Real Attestations Are Verified - (File: `crates/contract/src/lib.rs`)

### Summary
Both `init()` and `init_running()` set `accept_requests: true` unconditionally at contract initialization, while simultaneously populating the TEE state with **mock attestations** for all initial participants. The `accept_requests` flag is the sole runtime gate that blocks signing, CKD, and foreign-tx requests when TEE validation has failed. Because it defaults to `true`, the contract immediately accepts user signing requests and node `respond()` calls without any real TEE quote ever having been verified. An explicit call to `verify_tee()` is required to enforce the TEE invariant — mirroring exactly the `rollable = true` pattern in the Cooler report, where lenders had to separately toggle a flag that should have defaulted to the safe value.

### Finding Description

In `init()`:

```rust
// crates/contract/src/lib.rs:1944-1962
let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
Ok(Self {
    ...
    tee_state,
    accept_requests: true,   // ← permissive default
    ...
})
```

In `init_running()`:

```rust
// crates/contract/src/lib.rs:2024-2041
let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
Ok(MpcContract {
    ...
    tee_state,
    accept_requests: true,   // ← permissive default
    ...
})
```

`with_mocked_participant_attestations` stores `VerifiedAttestation::Mock(MockAttestation::Valid)` for every initial participant — no real DCAP/TDX quote is ever submitted or verified at this stage.

The `accept_requests` flag is checked in every security-critical path:

- `check_request_preconditions()` (called by `sign()`, `request_app_private_key()`, `verify_foreign_transaction()`) — panics only when `accept_requests == false`
- `respond()`, `respond_ckd()`, `respond_verify_foreign_tx()` — return `TeeError::TeeValidationFailed` only when `accept_requests == false`

The only mechanism to set `accept_requests = false` is `verify_tee()`, which must be called explicitly by a participant after initialization. Until that call is made, the contract operates as if all TEE checks have passed, regardless of whether any participant is actually running inside a TEE.

The Cooler parallel is exact:

| Cooler | NEAR MPC |
|--------|----------|
| `rollable = true` on loan creation | `accept_requests = true` on contract init |
| Lender must call `toggleRoll()` to restrict | Participant must call `verify_tee()` to restrict |
| Borrower can roll before lender acts | Nodes with mock/invalid attestations can sign before `verify_tee()` is called |

### Impact Explanation

During the window between contract initialization and the first successful `verify_tee()` call, all participants hold only mock attestations. Because `accept_requests = true`, the contract:

1. Accepts `sign()` / `request_app_private_key()` / `verify_foreign_transaction()` calls from any user.
2. Accepts `respond()` / `respond_ckd()` / `respond_verify_foreign_tx()` calls from participants whose only credential is a mock attestation — not a real hardware TEE quote.

This breaks the production safety invariant that threshold signatures are only produced inside verified TEEs. A set of participants running entirely outside TEEs (e.g., plain Linux processes) can complete the full signing lifecycle — DKG, key generation, and `respond()` — without the contract ever enforcing the TEE requirement, as long as `verify_tee()` has not been called.

This maps to the **Medium** allowed impact: *"contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."* The permissive default is structural, not a configuration error.

### Likelihood Explanation

- Every deployment of the contract starts in this state; there is no opt-in required to trigger it.
- The `verify_tee()` function is not called automatically; it requires an explicit participant transaction.
- Any delay in calling `verify_tee()` (e.g., during the keygen / resharing bootstrap phase, or after a contract migration via `init_running()`) extends the window.
- `init_running()` is `#[private]` but is invoked by the contract itself during upgrades, resetting `accept_requests` to `true` and re-populating mock attestations — meaning the window recurs on every migration.

### Recommendation

Set `accept_requests: false` in both `init()` and `init_running()`. Require at least one successful `verify_tee()` call — confirming that all initial participants hold valid, non-mock attestations — before the contract begins accepting signing requests. This mirrors the Cooler fix of defaulting `rollable` to `false` and requiring an explicit opt-in.

Alternatively, add a parameter to `init()` / `init_running()` that lets the deployer specify the initial value of `accept_requests`, so the safe default is `false` and an explicit `true` requires a deliberate choice.

### Proof of Concept

1. Deploy the contract via `init(parameters, None)`.
2. Observe that `accept_requests = true` and all participants have `VerifiedAttestation::Mock(MockAttestation::Valid)` — no real TEE quote submitted.
3. Vote to add a signing domain (`vote_add_domains`) and complete DKG (`start_keygen_instance` / `vote_pk`) — all participants pass `assert_caller_is_attested_participant_and_protocol_active()` because mock attestations are stored.
4. Call `sign(...)` as any user — `check_request_preconditions` passes because `accept_requests == true`.
5. Call `respond(request, signature)` from a participant account — passes because `accept_requests == true` and the mock attestation satisfies the participant check.
6. The threshold signature is produced and returned to the user. No real TEE hardware was involved at any step; `verify_tee()` was never called. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L298-302)
```rust
        // 4. Refuse the request if the contract is not currently accepting requests
        //    (e.g. because TEE validation has failed).
        if !self.accept_requests {
            env::panic_str(&TeeError::TeeValidationFailed.to_string())
        }
```

**File:** crates/contract/src/lib.rs (L579-581)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L1709-1710)
```rust
            TeeValidationResult::Full => {
                self.accept_requests = true;
```

**File:** crates/contract/src/lib.rs (L1944-1962)
```rust
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
```

**File:** crates/contract/src/lib.rs (L2023-2041)
```rust
        let initial_participants = parameters.participants();
        let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);

        Ok(MpcContract {
            config: init_config.map(Into::into).unwrap_or_default(),
            protocol_state: ProtocolContractState::Running(RunningContractState::new(
                domains,
                keyset,
                parameters,
                AddDomainsVotes::default(),
            )),
            pending_signature_requests: LookupMap::new(StorageKey::PendingSignatureRequestsV4),
            pending_ckd_requests: LookupMap::new(StorageKey::PendingCKDRequestsV3),
            pending_verify_foreign_tx_requests: LookupMap::new(
                StorageKey::PendingVerifyForeignTxRequestsV2,
            ),
            proposed_updates: Default::default(),
            tee_state,
            accept_requests: true,
```

**File:** crates/contract/src/tee/tee_state.rs (L103-143)
```rust
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
                account_id: account_id.clone(),
                tls_public_key: tls_public_key.clone(),
                // Use tls_public_key as account_public_key instead of hardcoded
                // Ed25519PublicKey::from([0u8; 32]) so that same account public
                // key isn't associated with different tls keys.
                // This is not a fix for above issue: #1087, which should be
                // addressed outside this PR.
                account_public_key: tls_public_key.clone(),
            };

            tee_state.stored_attestations.insert(
                tls_public_key,
                NodeAttestation {
                    node_id,
                    verified_attestation: VerifiedAttestation::Mock(
                        attestation::MockAttestation::Valid,
                    ),
                },
            );
        }

        tee_state
    }
```
