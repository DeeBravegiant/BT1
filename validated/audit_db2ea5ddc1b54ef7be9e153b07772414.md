### Title
Hardcoded Testing Private Key Used in Production `create_signature_manager()` — (`File: crates/apollo_signature_manager/src/lib.rs`)

### Summary
The production function `create_signature_manager()` unconditionally instantiates `LocalKeyStore::new_for_testing()`, which contains a hardcoded, publicly known ECDSA private key. This key is used to sign consensus precommit votes and peer identity challenges. Any party with access to the public repository can extract the private key and forge valid precommit vote signatures, impersonating the sequencer's consensus validator.

### Finding Description

`create_signature_manager()` in `crates/apollo_signature_manager/src/lib.rs` is the production factory for the `SignatureManager` component: [1](#0-0) 

It calls `SignatureManager::new()`, which calls `LocalKeyStore::new_for_testing()`: [2](#0-1) 

`LocalKeyStore::new_for_testing()` is **not** gated by `#[cfg(test)]`. It hardcodes a well-known private key directly in the source: [3](#0-2) 

This private key (`0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133`) and its corresponding public key (`0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a`) are committed in the public repository.

The `SignatureManager` uses this key for two operations:

- `sign_precommit_vote(block_hash)` — signs consensus precommit votes that finalize blocks
- `sign_identification(peer_id, challenge)` — signs peer identity challenges [4](#0-3) 

The `KeyStore::get_key()` implementation simply returns the hardcoded private key: [5](#0-4) 

A TODO comment in `lib.rs` acknowledges this is unresolved for production: [6](#0-5) 

### Impact Explanation

An attacker with read access to the repository (public) can:

1. Extract the hardcoded private key `0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133`.
2. Forge valid ECDSA precommit vote signatures over arbitrary `BlockHash` values.
3. Forge valid peer identity signatures, impersonating the sequencer node in the p2p layer.

Forged precommit votes bind a wrong block hash to a consensus round, causing validators that verify against the known public key to accept a fraudulent block commitment. This directly corrupts the committed block hash and state root visible to the rest of the network.

**Impact class:** Critical — wrong block/state commitment produced by broken consensus signature logic; the signer identity is fully compromised because the private key is public.

### Likelihood Explanation

Likelihood is **High**. The private key is in a committed, publicly readable source file. No special access, network position, or race condition is required. Any reader of the repository can reproduce the attack immediately.

### Recommendation

1. Remove `LocalKeyStore::new_for_testing()` from all non-`#[cfg(test)]` call sites.
2. Implement a production `KeyStore` that loads the private key from a secrets manager (e.g., via the existing `ExternalSecret` / Kubernetes secrets infrastructure already present in the deployment layer) or from a hardware security module, never from a hardcoded constant.
3. Gate `LocalKeyStore` and `new_for_testing()` behind `#[cfg(any(test, feature = "testing"))]` to prevent accidental reuse.
4. Rotate the exposed key immediately on any deployment that used this code path.

### Proof of Concept

```rust
// Attacker extracts from the public source file:
use starknet_api::block::BlockHash;
use starknet_api::felt;
use starknet_core::crypto::ecdsa_sign;
use starknet_crypto::Felt;

let private_key = Felt::from_hex_unchecked(
    "0x608bf2cdb1ad4138e72d2f82b8c5db9fa182d1883868ae582ed373429b7a133",
);

// Forge a precommit vote for an arbitrary block hash:
let fake_block_hash = BlockHash(felt!("0xdeadbeef"));
// ... build message digest using build_precommit_vote_message_digest (public domain separator)
// ... call ecdsa_sign(&private_key, &message_digest)
// Result: a valid signature that passes verify_precommit_vote_signature() for the
// production public key 0x125d56b1fbba593f1dd215b7c55e384acd838cad549c4a2b9c6d32d264f4e2a
```

The domain separator `PRECOMMIT_VOTE` and the `blake2s_to_felt` hashing logic are also public, so the full message digest is reproducible without any privileged access. [7](#0-6)

### Citations

**File:** crates/apollo_signature_manager/src/lib.rs (L18-20)
```rust
    pub fn new() -> Self {
        Self(GenericSignatureManager::new(LocalKeyStore::new_for_testing()))
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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L114-119)
```rust
#[async_trait]
impl KeyStore for LocalKeyStore {
    async fn get_key(&self) -> KeyStoreResult<PrivateKey> {
        Ok(self.private_key)
    }
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L138-145)
```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
}
```
