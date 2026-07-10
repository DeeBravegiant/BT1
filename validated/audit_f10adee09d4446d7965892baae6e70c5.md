### Title
`derived_public_key()` Returns Incorrect Key for `ForeignTx` Domains While `respond_verify_foreign_tx()` Verifies Against Root Key — (`File: crates/contract/src/lib.rs`)

---

### Summary

The `derived_public_key()` view function applies a user-path tweak to the root key for **any** domain, including `ForeignTx` domains. However, the actual `verify_foreign_transaction()` + `respond_verify_foreign_tx()` execution flow signs and verifies against the **root key with no tweak**. This is a direct analog to the EIP4626 preview-vs-execution mismatch: the "preview" (view) function returns a value that does not match what the actual execution produces, breaking the production safety invariant that `derived_public_key()` accurately previews the signing key.

---

### Finding Description

**The view function (`derived_public_key`):**

`derived_public_key()` accepts any `domain_id`, including `ForeignTx` domains. It unconditionally computes `tweak = derive_tweak(&predecessor, &path)` and returns `root_key + tweak * G`:

```rust
pub fn derived_public_key(
    &self,
    path: String,
    predecessor: Option<AccountId>,
    domain_id: Option<DomainId>,
) -> Result<dtos::PublicKey, Error> {
    let predecessor: AccountId = predecessor.unwrap_or_else(env::predecessor_account_id);
    let tweak = derive_tweak(&predecessor, &path);   // always applied
    let domain = domain_id.unwrap_or_else(DomainId::legacy_ecdsa_id);
    let public_key = self.public_key_extended(domain)?;
    // ... returns root_key + tweak * G for ALL domain types
```

There is no guard that rejects `ForeignTx` domains. [1](#0-0) 

**The actual execution (`respond_verify_foreign_tx`):**

`respond_verify_foreign_tx()` verifies the signature against the **root public key** (`secp_pk`), with no tweak applied. The comment in the test code is explicit: `// simulate signature with the root key (no tweak for foreign tx)`:

```rust
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,   // root key — no tweak
)
``` [2](#0-1) 

**The conversion function strips the derivation path entirely:**

`args_into_verify_foreign_tx_request()` converts the user-supplied args into the stored request without computing or storing any tweak:

```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
        // no tweak field — derivation_path is absent from the actual struct
    }
}
``` [3](#0-2) 

The `VerifyForeignTransactionRequestArgs` struct itself has no `derivation_path` field in the deployed code, unlike the design document's planned version: [4](#0-3) 

The test confirms the root-key-only behavior: [5](#0-4) 

**Contrast with `respond()` for Sign domains:**

For `Sign` domains, `respond()` correctly derives the key using `request.tweak` before verifying:

```rust
let expected_public_key =
    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
``` [6](#0-5) 

This confirms the asymmetry: `Sign` domains use a derived key (matching `derived_public_key()`), while `ForeignTx` domains use the root key (not matching `derived_public_key()`).

---

### Impact Explanation

The `ForeignChainSignatureVerifier::verify_signature()` in the NEAR MPC SDK takes a caller-supplied `public_key` parameter:

```rust
pub fn verify_signature(
    self,
    response: &VerifyForeignTransactionResponse,
    public_key: &PublicKey,
) -> Result<(), VerifyForeignChainError>
``` [7](#0-6) 

Any downstream contract or integrator that calls `derived_public_key(path, predecessor, foreign_tx_domain_id)` to obtain the expected verification key will receive `root_key + tweak * G` — a key that is **never used** for ForeignTx signing. Passing this key to `verify_signature()` will always produce `SignatureVerificationFailed`, causing every legitimate `verify_foreign_transaction` response to be rejected. This breaks the bridge execution flow: verified foreign-chain transactions cannot be confirmed by downstream contracts, and any funds or state transitions gated on that confirmation are permanently blocked.

This matches the **Medium** allowed impact: "contract execution-flow manipulation that breaks production safety/accounting invariants."

---

### Likelihood Explanation

The `derived_public_key()` function is the only on-chain API for computing the expected signing key for a given `(predecessor, path, domain_id)` tuple. Any integrator building a downstream contract that handles both `Sign` and `ForeignTx` domains will naturally call `derived_public_key()` for both. The function accepts `ForeignTx` domain IDs without error or warning, silently returning a wrong key. No privileged access is required — any unprivileged caller can trigger this path.

---

### Recommendation

Add a domain-purpose guard in `derived_public_key()` that panics or returns an error when called with a `ForeignTx` or `CKD` domain ID, since key derivation via tweak is only meaningful for `Sign` domains:

```rust
if domain_config.purpose != DomainPurpose::Sign {
    env::panic_str("derived_public_key is only valid for Sign domains");
}
```

Alternatively, once the planned `derivation_path` field is added to `VerifyForeignTransactionRequestArgs` and the ForeignTx flow is updated to use a tweak (as described in `docs/foreign-chain-transactions.md`), `derived_public_key()` should use the foreign-tx-specific tweak prefix for `ForeignTx` domains to match the actual signing behavior.

---

### Proof of Concept

1. Deploy the contract with a `ForeignTx` domain (e.g., `domain_id = 2`, `purpose = ForeignTx`, `protocol = CaitSith`).
2. Call `derived_public_key("my-path", "alice.near", 2)` → receives `K = root_key + derive_tweak("alice.near", "my-path") * G`.
3. Call `verify_foreign_transaction({domain_id: 2, request: bitcoin_tx, ...})` with 1 yoctoNEAR deposit.
4. MPC nodes sign the payload hash with the **root key** (no tweak) and call `respond_verify_foreign_tx()`.
5. The contract accepts the response (verified against root key internally).
6. A downstream contract calls `ForeignChainSignatureVerifier::verify_signature(response, &K)` using the key from step 2.
7. Verification fails with `SignatureVerificationFailed` because the signature was made with `root_key`, not `K`.
8. The downstream contract rejects the verified foreign transaction, permanently blocking any state transition gated on it.

The root cause is confirmed at:
- [1](#0-0)  (`derived_public_key` — no domain-purpose guard, always applies tweak)
- [8](#0-7)  (`respond_verify_foreign_tx` — verifies against root key, no tweak)
- [3](#0-2)  (`args_into_verify_foreign_tx_request` — no tweak computed or stored)

### Citations

**File:** crates/contract/src/lib.rs (L415-444)
```rust
    pub fn derived_public_key(
        &self,
        path: String,
        predecessor: Option<AccountId>,
        domain_id: Option<DomainId>,
    ) -> Result<dtos::PublicKey, Error> {
        let predecessor: AccountId = predecessor.unwrap_or_else(env::predecessor_account_id);
        let tweak = derive_tweak(&predecessor, &path);

        let domain = domain_id.unwrap_or_else(DomainId::legacy_ecdsa_id);
        let public_key = self.public_key_extended(domain)?;

        let derived_public_key: dtos::PublicKey = match public_key {
            PublicKeyExtended::Secp256k1 { near_public_key } => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
                    .as_affine();
                let derived_public_key =
                    derive_key_secp256k1(&affine, &tweak).map_err(PublicKeyError::from)?;
                derived_public_key.into()
            }
            PublicKeyExtended::Ed25519 { edwards_point, .. } => {
                let derived_public_key_edwards_point =
                    derive_public_key_edwards_point_ed25519(&edwards_point, &tweak);
                dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress()).into()
            }
            PublicKeyExtended::Bls12381 { public_key } => public_key,
        };
```

**File:** crates/contract/src/lib.rs (L597-598)
```rust
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;
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

**File:** crates/contract/src/lib.rs (L3694-3698)
```rust
        // simulate signature with the root key (no tweak for foreign tx)
        let secret_key_ec: elliptic_curve::SecretKey<Secp256k1> =
            elliptic_curve::SecretKey::from_bytes(&secret_key.to_bytes()).unwrap();
        let secret_key = SigningKey::from_bytes(&secret_key_ec.to_bytes()).unwrap();
        let (signature, recovery_id) = secret_key.sign_prehash_recoverable(&payload_hash).unwrap();
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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L101-105)
```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L42-47)
```rust
    pub fn verify_signature(
        self,
        response: &VerifyForeignTransactionResponse,
        // TODO(#2232): don't use interface API types for public keys
        public_key: &PublicKey,
    ) -> Result<(), VerifyForeignChainError> {
```
