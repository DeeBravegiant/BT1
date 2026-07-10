### Title
Missing Caller-Identity Binding in `verify_foreign_transaction` Causes Caller-Agnostic Root-Key Signing — (File: crates/contract/src/lib.rs, crates/node/src/providers/verify_foreign_tx/sign.rs)

### Summary

`verify_foreign_transaction()` discards the caller's identity (`predecessor_account_id`) when constructing the pending-request key and when deriving the signing tweak, unlike `sign()` and `request_app_private_key()` which both bind the caller into the request. As a result, every caller shares the same ForeignTx domain root key (zero tweak), the pending-request map is keyed without any caller field, and different callers submitting the same foreign-chain request receive an identical signature under the shared root key. The design document explicitly describes per-caller tweak derivation for this flow, but it was never implemented.

### Finding Description

**Root cause — contract side (`crates/contract/src/lib.rs`)**

`sign()` and `request_app_private_key()` both capture the caller:

```rust
// sign()
let (domain_config, predecessor) = self.check_request_preconditions(...);
let request = SignatureRequest::new(request.domain_id, request.payload, &predecessor, &request.path);
```

```rust
// request_app_private_key()
let (_, predecessor) = self.check_request_preconditions(...);
let request = CKDRequest::new(request.app_public_key, domain_id, &predecessor, &request.derivation_path);
```

`verify_foreign_transaction()` silently discards the return value of `check_request_preconditions`, so the predecessor is never captured:

```rust
// verify_foreign_transaction()
self.check_request_preconditions(          // ← return value discarded
    request.domain_id,
    DomainPurpose::ForeignTx,
    ...
);
let request = args_into_verify_foreign_tx_request(request);  // no predecessor
```

`args_into_verify_foreign_tx_request` is a trivial copy that carries no caller field:

```rust
pub fn args_into_verify_foreign_tx_request(args: VerifyForeignTransactionRequestArgs)
    -> VerifyForeignTransactionRequest {
    VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

The `VerifyForeignTransactionRequest` struct itself has no `predecessor` or `tweak` field, so the pending-request map key (`pending_verify_foreign_tx_requests`) is caller-agnostic.

**Root cause — node side (`crates/node/src/providers/verify_foreign_tx/sign.rs`)**

The node hard-codes a zero tweak when building the internal signing request:

```rust
Ok(SignatureRequest {
    ...
    tweak: Tweak::new([0u8; 32]),   // ← always root key, no caller derivation
    ...
})
```

**Root cause — contract verification (`crates/contract/src/lib.rs`)**

`respond_verify_foreign_tx` verifies the signature against the **root** public key, not a derived one:

```rust
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,          // ← root key, no tweak applied
)
```

Compare with `respond()` for `sign()`, which derives the expected key from `request.tweak`:

```rust
let expected_public_key =
    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
```

**Design intent vs. implementation**

`docs/foreign-chain-transactions.md` explicitly specifies that `verify_foreign_transaction` should derive a per-caller tweak using a foreign-tx-specific prefix, and that `VerifyForeignTransactionRequest` should carry a `tweak` field and `VerifyForeignTransactionRequestArgs` should carry a `derivation_path`. Neither field exists in the actual structs, and no tweak derivation is performed anywhere in the production path.

### Impact Explanation

1. **Caller-agnostic request key**: Because `VerifyForeignTransactionRequest` has no caller field, any two accounts submitting the same `ForeignChainRpcRequest` share a single pending-request map entry. The existing test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` confirms this: Alice and Bob both receive the same `VerifyForeignTransactionResponse`. This breaks the request-lifecycle invariant that holds for `sign()` and `request_app_private_key()`.

2. **Shared root key for all callers**: Every `verify_foreign_transaction` response is signed under the ForeignTx domain root key (zero tweak). Bridge contracts that follow the design document's intended API — verifying the response against a per-caller derived key — will always fail signature verification. Bridge contracts that instead verify against the root key cannot distinguish between callers, enabling cross-caller response substitution: a response obtained by any party for transaction T is equally valid for any other party's bridge contract that accepts root-key signatures for T.

3. **Broken accounting invariant**: The contract's own design mandates that the caller's identity be bound into the signing context (as it is for `sign()` and `request_app_private_key()`). The missing binding means the ForeignTx signing domain does not provide per-caller key isolation, violating the stated safety invariant and the domain-separation guarantee described in the design document.

**Severity**: Medium — request-lifecycle and participant-state invariant broken; no direct fund theft, but bridge contracts relying on caller-specific key isolation will malfunction or accept cross-caller responses.

### Likelihood Explanation

Any unprivileged NEAR account can call `verify_foreign_transaction()` with a valid foreign-chain transaction. No special access is required. The missing caller binding is triggered on every invocation of the function, making this a 100% reproducible condition for any caller.

### Recommendation

1. Add a `derivation_path: String` field to `VerifyForeignTransactionRequestArgs` and derive the tweak inside `verify_foreign_transaction()` using the captured `predecessor` and the foreign-tx-specific prefix (as described in `docs/foreign-chain-transactions.md`).
2. Add a `tweak: Tweak` field to `VerifyForeignTransactionRequest` and store the derived tweak in the pending-request key.
3. Update `args_into_verify_foreign_tx_request` (or replace it) to accept the predecessor and compute the tweak.
4. Update the node's `build_signature_request` in `crates/node/src/providers/verify_foreign_tx/sign.rs` to read the tweak from the request rather than hard-coding `[0u8; 32]`.
5. Update `respond_verify_foreign_tx` to derive the expected public key from the stored tweak (mirroring `respond()`).

### Proof of Concept

**Step 1**: Alice calls `verify_foreign_transaction({tx_id: X, domain_id: ForeignTx, ...})`. The contract calls `check_request_preconditions` but discards the returned `predecessor`. [1](#0-0) 

**Step 2**: `args_into_verify_foreign_tx_request` produces a `VerifyForeignTransactionRequest` with no caller field. [2](#0-1) 

**Step 3**: The request is stored in `pending_verify_foreign_tx_requests` under a caller-agnostic key. [3](#0-2) 

**Step 4**: Bob calls `verify_foreign_transaction` with the same arguments. Because the key has no caller field, Bob's yield is appended to Alice's queue entry (confirmed by the existing test). [4](#0-3) 

**Step 5**: The MPC node builds the signing request with a zero tweak. [5](#0-4) 

**Step 6**: The contract verifies the response against the root public key (no tweak applied), and both Alice and Bob receive the identical response signed under the shared root key. [6](#0-5) 

**Contrast with `sign()`**, which derives the expected key from `request.tweak` (caller-specific): [7](#0-6) 

**Design document specifying the intended (unimplemented) per-caller tweak derivation**: [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L526-531)
```rust
        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );
```

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L597-608)
```rust
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
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

**File:** crates/contract/src/lib.rs (L3208-3263)
```rust
    #[test]
    fn verify_foreign_transaction__should_queue_duplicates_from_different_callers() {
        // Given: two different callers will submit the same foreign-tx verification request.
        let mut rng = rand::rngs::StdRng::from_seed([42u8; 32]);
        let (context, mut contract, secret_key) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut rng);
        register_supported_chains(&mut contract, [dtos::ForeignChain::Bitcoin]);
        let SharedSecretKey::Secp256k1(secret_key) = secret_key else {
            unreachable!();
        };

        let request_args = VerifyForeignTransactionRequestArgs {
            domain_id: DomainId::default().0.into(),
            payload_version: ForeignTxPayloadVersion::V1,
            request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
                tx_id: [7u8; 32].into(),
                confirmations: 2.into(),
                extractors: vec![BitcoinExtractor::BlockHash],
            }),
        };
        let request = args_into_verify_foreign_tx_request(request_args.clone());

        // When: caller alice submits the request.
        let alice = AccountId::from_str("alice.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(alice.clone())
                .predecessor_account_id(alice)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args.clone());

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L39-47)
```rust
    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
```

**File:** docs/foreign-chain-transactions.md (L254-286)
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

This ensures key material used for validated foreign transactions is **always** distinct from
general-purpose `sign()` keys, even if the same account and derivation path are reused.
```
