### Title
Missing Caller-Context Isolation in `verify_foreign_transaction` Allows Cross-User Signature Sharing and Breaks Caller-Binding Invariant - (File: crates/near-mpc-contract-interface/src/types/foreign_chain.rs)

### Summary

The design specification for `verify_foreign_transaction` explicitly requires a `derivation_path` field in `VerifyForeignTransactionRequestArgs` and a `tweak` field (derived from `predecessor_id + derivation_path`) in `VerifyForeignTransactionRequest`, so that each caller receives a signature under their own derived key. The production implementation omits both fields entirely. As a result, the `ForeignTx` domain's **root key** signs every foreign-chain attestation, the pending-request map key contains no caller identity, and any unprivileged account can piggyback on any other account's pending request to receive the same root-key signature — breaking the caller-binding safety invariant the protocol was designed to enforce.

### Finding Description

**Design intent vs. implementation gap:**

The design document (`docs/foreign-chain-transactions.md`) specifies:

```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub derivation_path: String,   // ← required by design
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}

pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub tweak: Tweak,              // ← required by design
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

The actual production structs contain neither field:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs:101-128
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // NO derivation_path, NO predecessor binding
}

pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // NO tweak, NO caller identity
}
```

The conversion function `args_into_verify_foreign_tx_request` simply copies the three fields with no tweak derivation:

```rust
// crates/contract/src/dto_mapping.rs:840-848
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

**Root-key signing confirmed by contract and tests:**

`respond_verify_foreign_tx` verifies the signature against the **root public key** with no tweak applied — the opposite of `respond`, which always applies `derive_key_secp256k1(&affine, &request.tweak)`:

```rust
// crates/contract/src/lib.rs:718-734
let signature_is_valid = match (&response.signature, public_key) {
    (
        dtos::SignatureResponse::Secp256k1(signature_response),
        PublicKeyExtended::Secp256k1 { near_public_key },
    ) => {
        let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)...;
        let payload_hash: [u8; 32] = response.payload_hash.0;
        // Check the signature is correct against the root public key
        near_mpc_signature_verifier::verify_ecdsa_signature(
            signature_response,
            &payload_hash,
            &secp_pk,   // ← root key, no tweak
        ).is_ok()
    }
```

The test comment at line 3694 confirms this explicitly: `"simulate signature with the root key (no tweak for foreign tx)"`.

**Caller-agnostic request key enables cross-user sharing:**

Because `VerifyForeignTransactionRequest` contains no caller identity, the `pending_verify_foreign_tx_requests` map key is identical for any two accounts submitting the same `(chain, tx_id, extractors, domain_id, payload_version)` tuple. The contract queues them under one entry and fans the single root-key signature out to all of them:

```rust
// crates/contract/src/lib.rs:3255-3262 (test comment)
// Then: both yields are queued under the single (caller-agnostic) request key.
assert_eq!(
    contract.pending_verify_foreign_tx_requests.get(&request).map(|q| q.len()),
    Some(2),
    "duplicate foreign-tx requests from different callers should fan out",
);
```

**Attacker entry path:**

1. Alice submits `verify_foreign_transaction` for Bitcoin tx `0xABC` to trigger a bridge credit.
2. Attacker Bob submits the identical request (same `tx_id`, same `domain_id`, same `extractors`) with 1 yoctoNEAR deposit — the only precondition.
3. Both are queued under the same caller-agnostic key.
4. MPC nodes respond once with a root-key signature over `SHA256(borsh(ForeignTxSignPayloadV1 { request, values }))`.
5. Bob receives the identical `VerifyForeignTransactionResponse` (same `payload_hash`, same root-key signature) as Alice.
6. Bob presents this response to any bridge contract that trusts `verify_foreign_transaction` output without independently binding the response to the original submitter.

### Impact Explanation

**Impact: Medium** — request-lifecycle and contract execution-flow manipulation that breaks the production caller-binding safety invariant.

The design explicitly states: *"The contract derives the tweak internally from `request.derivation_path` (callers do not submit raw tweaks)"* and *"key material used for validated foreign transactions is always distinct from general-purpose `sign()` keys, even if the same account and derivation path are reused."* Neither guarantee holds in production:

- The `ForeignTx` domain root key is exposed as a shared signing oracle to every unprivileged caller.
- Any caller can obtain a root-key attestation over any foreign-chain transaction without being the originating party.
- Bridge contracts built on `verify_foreign_transaction` that rely on the response being caller-specific (e.g., to credit only the submitting account) are vulnerable to a free-rider / front-running attack where an adversary submits the same request and receives the same attestation.
- The missing tweak also means the `ForeignTx` root key signs payloads that were designed to be caller-scoped, weakening the domain-separation guarantee between `Sign` and `ForeignTx` domains.

### Likelihood Explanation

**Likelihood: High.** The attack requires only a 1 yoctoNEAR deposit and knowledge of a pending `verify_foreign_transaction` request (observable on-chain). No privileged access, leaked keys, or threshold collusion is needed. The fan-out behavior is already tested and confirmed to work across different callers.

### Recommendation

1. Add `derivation_path: String` to `VerifyForeignTransactionRequestArgs` and derive `tweak = derive_foreign_tx_tweak(predecessor_id, derivation_path)` inside `verify_foreign_transaction`, storing it in `VerifyForeignTransactionRequest`.
2. Update `respond_verify_foreign_tx` to verify the signature against the **derived** public key (applying the stored tweak), mirroring the `respond` function.
3. Include the tweak in the pending-request map key so that requests from different callers are stored and resolved independently.
4. Update `args_into_verify_foreign_tx_request` to accept `predecessor_id` and perform the tweak derivation, consistent with the design document.

### Proof of Concept

**Root cause files:**

- `VerifyForeignTransactionRequestArgs` missing `derivation_path`: [1](#0-0) 
- `VerifyForeignTransactionRequest` missing `tweak`: [2](#0-1) 
- `args_into_verify_foreign_tx_request` performs no tweak derivation: [3](#0-2) 
- `respond_verify_foreign_tx` verifies against root key, no tweak: [4](#0-3) 
- Caller-agnostic fan-out confirmed in test: [5](#0-4) 

**Design doc specifying the missing fields (not implemented):** [6](#0-5) 

**Design doc specifying the missing tweak derivation prefix (not implemented):** [7](#0-6) 

**Contrast with `respond` which correctly applies the tweak:** [8](#0-7)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L101-105)
```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
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

**File:** crates/contract/src/lib.rs (L596-608)
```rust
                    .as_affine();
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

**File:** crates/contract/src/lib.rs (L3255-3263)
```rust
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

**File:** docs/foreign-chain-transactions.md (L98-110)
```markdown
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub derivation_path: String, // Key derivation path
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}

pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub tweak: Tweak,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
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
