### Title
Hardcoded Validator Private Key Used in Production `create_signature_manager()` — (`File: crates/apollo_signature_manager/src/lib.rs`)

### Summary
The production `create_signature_manager()` function unconditionally calls `LocalKeyStore::new_for_testing()`, which embeds a well-known ECDSA private key directly in source. Every sequencer node therefore shares the same signing identity. Any party with source access can forge precommit-vote signatures and peer-identity proofs for any node, breaking the consensus signature-verification invariant.

### Finding Description

`LocalKeyStore::new_for_testing()` hard-codes both the private and public ECDSA keys:

```rust
// crates/apollo_signature_manager/src/signature_manager.rs
pub(crate) const fn new_for_testing() -> Self {
    const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
        "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
    ));
    const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
        "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
    ));
    Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
}
``` [1](#0-0) 

The production factory function `create_signature_manager()` calls `SignatureManager::new()`, which in turn calls `LocalKeyStore::new_for_testing()` — there is no conditional, no feature flag, and no alternative code path:

```rust
// crates/apollo_signature_manager/src/lib.rs
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()   // → LocalKeyStore::new_for_testing()
}
``` [2](#0-1) 

The accompanying TODO confirms this is an unresolved placeholder, not an intentional test-only path:

> `// TODO(Elin): understand how key store would look in production and better define the way the signature manager is created.` [3](#0-2) 

The `SignatureManager` is wired into the live node via `crates/apollo_node/src/components.rs` and `crates/apollo_node/src/clients.rs`. It is used for two consensus-critical operations:

1. **`sign_precommit_vote(block_hash)`** — produces the ECDSA signature that a validator attaches to its PRECOMMIT message, binding it to a specific `BlockHash`.
2. **`sign_identification(peer_id, challenge)`** — authenticates the node's identity during the P2P handshake. [4](#0-3) 

Because every deployed node loads the same private key from source, all nodes are cryptographically indistinguishable from one another, and the key is public knowledge to anyone who reads the repository.

### Impact Explanation

**Consensus signature forgery (High → Critical boundary):**
An attacker who knows the private key can call `ecdsa_sign(PRIVATE_KEY, precommit_digest)` offline and produce a valid precommit signature for *any* `BlockHash`. If the consensus layer accepts precommit votes verified against the well-known public key, the attacker can inject forged PRECOMMIT messages that appear to originate from a legitimate validator, potentially driving consensus toward an attacker-chosen block hash.

**Peer-identity impersonation:**
The same key is used for `sign_identification`. An attacker can pass any peer-identity challenge, impersonating any node in the P2P layer.

**Uniform key across all nodes:**
Because the key is a compile-time constant shared by every node, the entire validator set is compromised simultaneously — not just a single node.

This maps to: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"* (High) and, if consensus accepts the forged votes to finalize an invalid block, escalates to *"Invalid or unauthorized Starknet transaction accepted"* (Critical).

### Likelihood Explanation

**Low effort, high reach.** The private key is a public constant in an open repository. No privileged access, no network position, and no special tooling are required — only the ability to read the source and call a standard ECDSA signing library with the known scalar.

### Recommendation

1. **Short term:** Remove `LocalKeyStore::new_for_testing()` from the production call path. Gate it behind `#[cfg(test)]` so it cannot be reached from `create_signature_manager()`.
2. **Short term:** Rotate the exposed key pair immediately; treat the public key as permanently compromised.
3. **Long term:** Implement a proper `KeyStore` backend that reads the private key from a secret-management solution (e.g., HashiCorp Vault, GCP Secret Manager, or an HSM) at startup, consistent with the existing `Sensitive<T>` wrapper pattern already present in `crates/apollo_config/src/secrets.rs`. [5](#0-4) 

### Proof of Concept

```rust
use starknet_core::crypto::ecdsa_sign;
use starknet_types_core::felt::Felt;

// Private key is public knowledge — read directly from source.
let private_key = Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
);

// Forge a PRECOMMIT vote for an arbitrary block hash.
let target_block_hash = Felt::from_hex_unchecked("0xdeadbeef...");
let mut message = b"PRECOMMIT_VOTE".to_vec();
message.extend_from_slice(&target_block_hash.to_bytes_be());
let digest = blake2s_to_felt(&message);

// Produces a signature indistinguishable from a legitimate validator's vote.
let forged_signature = ecdsa_sign(&private_key, &digest).unwrap();
// Submit forged_signature as a PRECOMMIT for target_block_hash to the consensus layer.
```

The forged signature will pass `verify_precommit_vote_signature` because the verifier uses the matching public key `0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a`, which is also hardcoded in source. [6](#0-5)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L68-82)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }

    async fn sign(&self, message_digest: MessageDigest) -> SignatureManagerResult<RawSignature> {
        let private_key = self.keystore.get_key().await?;
        let signature = ecdsa_sign(&private_key, &message_digest)
            .map_err(|e| SignatureManagerError::Sign(e.to_string()))?;

        Ok(signature.into())
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L101-111)
```rust
    pub(crate) const fn new_for_testing() -> Self {
        // Created using `cairo-lang`.
        const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
            "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
        ));
        const PUBLIC_KEY: PublicKey = PublicKey(Felt::from_hex_unchecked(
            "0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a",
        ));

        Self { private_key: PRIVATE_KEY, public_key: PUBLIC_KEY }
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L179-186)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_signature_manager/src/lib.rs (L39-43)
```rust
// TODO(Elin): understand how key store would look in production and better define the way the
// signature manager is created.
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
```

**File:** crates/apollo_config/src/secrets.rs (L24-51)
```rust
/// A wrapper for values that are considered **sensitive** (e.g. secrets, tokens, URLs).
#[derive(Clone, Deserialize)]
#[serde(transparent, bound(deserialize = "T: Deserialize<'de>"))]
pub struct Sensitive<T> {
    inner: T,
    #[serde(skip)]
    redactor: Option<Redactor<T>>,
}

impl<T> Sensitive<T> {
    /// Creates a new `Sensitive<T>` with no custom redactor.
    pub fn new(inner: T) -> Self {
        Self { inner, redactor: None }
    }

    /// Attaches a custom redactor function to this `Sensitive` value.
    pub fn with_redactor<F>(mut self, redactor: F) -> Self
    where
        F: Fn(&T) -> String + Send + Sync + 'static,
    {
        self.redactor = Some(Arc::new(redactor));
        self
    }

    /// Consumes the wrapper and returns the inner sensitive value.
    pub fn expose_secret(self) -> T {
        self.inner
    }
```
