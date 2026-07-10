### Title
Missing Caller Identity (Predecessor) in `verify_foreign_transaction` Request — Signing Always Uses Root Domain Key Instead of Caller-Derived Key - (File: crates/contract/src/lib.rs)

### Summary

The `verify_foreign_transaction` function in `MpcContract` discards the predecessor (caller account ID) returned by `check_request_preconditions`, and the `VerifyForeignTransactionRequest` struct contains no `tweak` field. As a result, every foreign-chain verification request is signed with the **root domain key** rather than a caller-specific derived key. This is directly analogous to the Chakra M-03 finding: the caller's identity is captured (logged) but never bound into the stored request or the signing operation, breaking the per-caller key-isolation invariant that `sign()` and `request_app_private_key()` both enforce.

---

### Finding Description

`check_request_preconditions` returns a `(DomainConfig, AccountId)` tuple where the second element is the verified `predecessor_account_id`. In `sign()` and `request_app_private_key()`, this predecessor is used to derive a caller-specific `Tweak` that is stored in the request struct and later used to select the correct derived signing key:

```rust
// sign() — correct
let (domain_config, predecessor) = self.check_request_preconditions(...);
let request = SignatureRequest::new(request.domain_id, request.payload, &predecessor, &request.path);
//                                                                         ^^^^^^^^^^^
```

```rust
// request_app_private_key() — correct
let (_, predecessor) = self.check_request_preconditions(...);
let request = CKDRequest::new(request.app_public_key, domain_id, &predecessor, &request.derivation_path);
//                                                                 ^^^^^^^^^^^
```

In `verify_foreign_transaction()`, the return value is **completely discarded**:

```rust
// verify_foreign_transaction() — BUG
self.check_request_preconditions(          // ← return value not bound
    request.domain_id,
    DomainPurpose::ForeignTx,
    Gas::from_tgas(...),
    MINIMUM_SIGN_REQUEST_DEPOSIT,
);
// ...
let request = args_into_verify_foreign_tx_request(request);  // no predecessor, no tweak
```

`args_into_verify_foreign_tx_request` simply copies the three fields (`domain_id`, `request`, `payload_version`) with no tweak computation:

```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
        // no tweak field
    }
}
```

The `VerifyForeignTransactionRequest` struct itself has no `tweak` field:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // tweak is absent
}
```

Consequently, `respond_verify_foreign_tx` verifies the signature against the **root public key** with no derivation applied — unlike `respond()`, which applies `derive_key_secp256k1(&affine, &request.tweak)`:

```rust
// respond_verify_foreign_tx — root key, no tweak
let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)...;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,   // ← root key, no derivation
)
```

The design document (`docs/foreign-chain-transactions.md`) explicitly specifies that `verify_foreign_transaction()` **must** derive a tweak from `(predecessor_id, derivation_path)` using a distinct prefix, and that `VerifyForeignTransactionRequestArgs` must carry a `derivation_path` field and `VerifyForeignTransactionRequest` must carry a `tweak` field. Neither field exists in the production code.

---

### Impact Explanation

**Medium — request-lifecycle and contract execution-flow invariant broken.**

1. **No per-caller key isolation.** Every caller of `verify_foreign_transaction`, regardless of account identity, receives a signature produced under the same root `ForeignTx` domain key. The design intent — that each `(predecessor, derivation_path)` pair maps to a unique derived key — is entirely absent.

2. **Cross-caller request collision.** Because `VerifyForeignTransactionRequest` contains no caller-specific field, two different callers submitting the same `ForeignChainRpcRequest` produce an identical map key. Their yields are queued under the same entry and both receive the same signature. The test at line 3208 documents this as a known limitation ("a different account would today be blocked from receiving a response by alice's submission"), but the root cause is the missing predecessor binding.

3. **Design-specified domain separation not enforced at the key level.** The design doc requires a distinct tweak prefix (`"near-mpc-recovery v0.1.0 foreign-tx epsilon derivation:"`) so that a `(predecessor, path)` pair can never yield the same derived key for `ForeignTx` and `Sign` domains. Without any tweak, the `ForeignTx` domain always signs with the root key, making the per-caller key-isolation guarantee meaningless for this flow.

4. **Off-chain consumers receive misleading data.** The predecessor is logged at line 521 but is not bound into the stored request. Any off-chain indexer or bridge service that reconstructs "which account initiated this foreign-tx verification" from on-chain state will find no such field, mirroring the Chakra M-03 pattern of logged-but-not-stored identity.

---

### Likelihood Explanation

**High.** The code path is reachable by any unprivileged NEAR account that attaches 1 yoctoNEAR and calls `verify_foreign_transaction` with a supported foreign chain. No special role, collusion, or privileged access is required. Every invocation of the function is affected.

---

### Recommendation

1. Add `derivation_path: String` to `VerifyForeignTransactionRequestArgs`.
2. In `verify_foreign_transaction()`, bind the predecessor from `check_request_preconditions` and compute the tweak using a `ForeignTx`-specific prefix before constructing the request.
3. Add a `tweak` field to `VerifyForeignTransactionRequest` and populate it from step 2.
4. Update `respond_verify_foreign_tx` to verify the signature against the **derived** public key (applying the stored tweak), matching the pattern used in `respond()`.

---

### Proof of Concept

**Step 1 — `sign()` correctly binds the predecessor:** [1](#0-0) 

**Step 2 — `verify_foreign_transaction()` discards the return value of `check_request_preconditions`:** [2](#0-1) 

**Step 3 — `args_into_verify_foreign_tx_request` copies three fields with no tweak:** [3](#0-2) 

**Step 4 — `VerifyForeignTransactionRequest` has no `tweak` field:** [4](#0-3) 

**Step 5 — `respond_verify_foreign_tx` verifies against the root key (no tweak), unlike `respond()`:** [5](#0-4) 

**Step 6 — Design doc specifies `derivation_path` in args and `tweak` in request (not implemented):** [6](#0-5) 

**Step 7 — Test explicitly documents the cross-caller collision caused by the missing predecessor binding:** [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L352-384)
```rust
        let (domain_config, predecessor) = self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::Sign,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        // ensure the signer sent a valid signature request
        // It's important we fail here because the MPC nodes will fail in an identical way.
        // This allows users to get the error message
        match domain_config.protocol {
            Protocol::CaitSith | Protocol::DamgardEtAl => {
                let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
                k256::Scalar::from_repr(hash.into())
                    .into_option()
                    .expect("Ecdsa payload cannot be converted to Scalar");
            }
            Protocol::Frost => {
                request.payload.as_eddsa().expect("Payload is not EdDSA");
            }
            Protocol::ConfidentialKeyDerivation => {
                env::panic_str(
                    "ConfidentialKeyDerivation is not supported for signature responses",
                );
            }
        }

        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L526-557)
```rust
        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        let requested_chain = request.request.chain();
        let supported_chains = self.get_supported_foreign_chains();
        if !supported_chains.contains(&requested_chain) {
            env::panic_str(
                &InvalidParameters::ForeignChainNotSupported {
                    requested: requested_chain,
                }
                .to_string(),
            );
        }

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
    }
```

**File:** crates/contract/src/lib.rs (L718-734)
```rust
        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L3241-3263)
```rust

        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
        let bob = AccountId::from_str("bob.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(bob.clone())
                .predecessor_account_id(bob)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args);

        // Then: both yields are queued under the single (caller-agnostic) request key.
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** docs/foreign-chain-transactions.md (L254-283)
```markdown
## Tweak Derivation (Sign vs ForeignTx)

`verify_foreign_transaction()` uses a **different tweak derivation prefix** than `sign()` so the same
`(predecessor_id, derivation_path)` can never yield the same derived key across the two purposes.

Design:

* Keep the existing sign tweak derivation prefix unchanged.
* Introduce a foreign-tx-specific prefix and derive the tweak from the same `(predecessor_id, derivation_path)`
  input using the same hash construction.
* The contract derives the tweak internally from `request.derivation_path` (callers do not submit raw tweaks).

Example:

```rust
const SIGN_TWEAK_DERIVATION_PREFIX: &str =
    "near-mpc-recovery v0.1.0 epsilon derivation:";
const FOREIGN_TX_TWEAK_DERIVATION_PREFIX: &str =
    "near-mpc-recovery v0.1.0 foreign-tx epsilon derivation:";

pub fn derive_sign_tweak(predecessor_id: &AccountId, path: &str) -> Tweak {
    let hash: [u8; 32] = derive_from_path(SIGN_TWEAK_DERIVATION_PREFIX, predecessor_id, path);
    Tweak::new(hash)
}

pub fn derive_foreign_tx_tweak(predecessor_id: &AccountId, path: &str) -> Tweak {
    let hash: [u8; 32] = derive_from_path(FOREIGN_TX_TWEAK_DERIVATION_PREFIX, predecessor_id, path);
    Tweak::new(hash)
}
```
```
