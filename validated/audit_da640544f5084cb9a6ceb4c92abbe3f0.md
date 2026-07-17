### Title
Unbounded `inner`/`metadata` Signature Separation in `ChunkEndorsementV2::verify` Enables Cache-Poisoning Endorsement Suppression — (`core/primitives/src/stateless_validation/chunk_endorsement.rs`)

---

### Summary

`ChunkEndorsementV2` signs `inner` (containing `chunk_hash`) and `metadata` (containing `shard_id`, `epoch_id`, `height_created`) as two independent Borsh blobs with no cryptographic binding between them. A network peer who observes two legitimate endorsements from the same validator for different heights on the same shard can splice `inner` from endorsement A with `metadata` from endorsement B, producing a forged `ChunkEndorsementV2` that passes `verify()`. When this forged message reaches a block-producer node first, it occupies the per-validator cache slot for chunk B's `ChunkProductionKey`. The subsequent legitimate endorsement for chunk B is then silently deduplicated and discarded. `collect_chunk_endorsements` later filters the poisoned entry out (wrong `chunk_hash`), so the validator's stake is never counted, potentially preventing chunk B from reaching the 2/3 endorsement threshold.

---

### Finding Description

**Root cause — no binding between the two signed payloads.**

`ChunkEndorsement::new` creates and signs two independent structures:

```rust
// inner signs only chunk_hash
let inner = ChunkEndorsementInnerV1::new(chunk_header.chunk_hash().clone());
let signature = signer.sign_bytes(&borsh::to_vec(&inner).unwrap());

// metadata signs only shard_id / epoch_id / height_created
let metadata_signature = signer.sign_bytes(&borsh::to_vec(&metadata).unwrap());
``` [1](#0-0) 

`ChunkEndorsementV2::verify` then checks each signature independently:

```rust
fn verify(&self, public_key: &PublicKey) -> bool {
    let inner    = borsh::to_vec(&self.inner).unwrap();
    let metadata = borsh::to_vec(&self.metadata).unwrap();
    self.signature.verify(&inner, public_key)
        && self.metadata_signature.verify(&metadata, public_key)
}
``` [2](#0-1) 

Because neither signed payload references the other, any two valid endorsements from the same validator can be mixed: `inner` from endorsement A (chunk_hash = H_A, height H1) combined with `metadata` from endorsement B (height H2) produces a struct where both `self.signature` and `self.metadata_signature` verify correctly against the validator's public key.

**Cache-poisoning path.**

`process_chunk_endorsement` derives the cache key exclusively from `metadata`:

```rust
let key = endorsement.chunk_production_key(); // uses metadata.{shard_id, epoch_id, height_created}
``` [3](#0-2) 

After the forged endorsement passes `validate_chunk_endorsement_signature` (which calls `endorsement.verify()`), it is stored:

```rust
cache.get_or_insert_mut(key, || HashMap::new()).insert(
    account_id.clone(),
    (endorsement.chunk_hash(), endorsement.signature()),  // stores H_A under chunk B's key
);
``` [4](#0-3) 

The deduplication guard then permanently blocks the legitimate endorsement for chunk B:

```rust
if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
    return Ok(()); // legitimate endorsement B silently dropped
}
``` [5](#0-4) 

**Suppression at collection time.**

`collect_chunk_endorsements` filters by `chunk_hash`:

```rust
.filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
``` [6](#0-5) 

The poisoned entry has `chunk_hash = H_A ≠ H_B`, so the validator's stake is never added to `endorsed_stake`, and `is_endorsed` may remain `false`.

---

### Impact Explanation

A malicious peer can selectively suppress any targeted validator's endorsement for any specific chunk height. If the targeted validator(s) hold stake that is critical to the 2/3 threshold, chunk B will not be endorsed. The block producer cannot include chunk B as a new chunk, causing a consensus liveness failure for that shard at that height. The attack is permanent for the lifetime of the cache entry (the LRU cache holds 100 entries). [7](#0-6) 

---

### Likelihood Explanation

The attacker must be a network peer who:
1. Observes two valid endorsements from the same validator for different heights on the same shard (both are broadcast over P2P).
2. Relays the forged endorsement to the block-producer node before the legitimate endorsement B arrives.

No validator key material is needed. The only requirement is network positioning to win the race. This is realistic for an adversary with a well-connected peer or a man-in-the-middle position on the endorsement gossip path.

---

### Recommendation

Bind `inner` and `metadata` cryptographically in a single signed payload. The simplest fix is to sign a combined struct:

```rust
struct ChunkEndorsementSignedData {
    chunk_hash: ChunkHash,
    shard_id: ShardId,
    epoch_id: EpochId,
    height_created: BlockHeight,
    account_id: AccountId,
    signature_differentiator: SignatureDifferentiator,
}
```

and produce a single `signature` over its Borsh serialization, eliminating the separate `metadata_signature` field entirely. This is the pattern already used by `SpiceChunkEndorsement`, which signs `SpiceEndorsementSignedData` (containing both `execution_result_hash` and `chunk_id`) as one atomic payload. [8](#0-7) 

---

### Proof of Concept

```rust
// Pseudocode — all types from production code
let signer = ValidatorSigner::new(validator_key);

// Two legitimate endorsements from the same validator, same shard/epoch, different heights
let endorsement_a = ChunkEndorsement::new(epoch_id, &chunk_header_a, &signer); // height H1, hash H_A
let endorsement_b = ChunkEndorsement::new(epoch_id, &chunk_header_b, &signer); // height H2, hash H_B

// Extract internals (both are ChunkEndorsement::V2)
let ChunkEndorsement::V2(v2_a) = endorsement_a;
let ChunkEndorsement::V2(v2_b) = endorsement_b;

// Forge: inner from A (chunk_hash=H_A, sig_A) + metadata from B (height=H2, metadata_sig_B)
let forged = ChunkEndorsement::V2(ChunkEndorsementV2 {
    inner:              v2_a.inner,              // chunk_hash = H_A
    signature:          v2_a.signature,          // valid sig over H_A
    metadata:           v2_b.metadata,           // height_created = H2
    metadata_signature: v2_b.metadata_signature, // valid sig over H2 metadata
});

// Step 1: verify() returns true — no binding check
assert!(forged.verify(&signer.public_key()));

// Step 2: routes to chunk B's ChunkProductionKey
assert_eq!(forged.chunk_production_key(), chunk_b_key);

// Step 3: process_chunk_endorsement accepts and caches it under chunk B's key with H_A
tracker.process_chunk_endorsement(&forged).unwrap();

// Step 4: legitimate endorsement B is now deduplicated and dropped
tracker.process_chunk_endorsement(&endorsement_b).unwrap(); // silently ignored

// Step 5: collect_chunk_endorsements for chunk B filters out the poisoned entry
let state = tracker.collect_chunk_endorsements(&chunk_header_b).unwrap();
assert!(!state.is_endorsed); // validator's stake not counted
```

### Citations

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L34-37)
```rust
        let metadata_signature = signer.sign_bytes(&borsh::to_vec(&metadata).unwrap());
        let inner = ChunkEndorsementInnerV1::new(chunk_header.chunk_hash().clone());
        let signature = signer.sign_bytes(&borsh::to_vec(&inner).unwrap());
        let endorsement = ChunkEndorsementV2 { inner, signature, metadata, metadata_signature };
```

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L112-117)
```rust
    fn verify(&self, public_key: &PublicKey) -> bool {
        let inner = borsh::to_vec(&self.inner).unwrap();
        let metadata = borsh::to_vec(&self.metadata).unwrap();
        self.signature.verify(&inner, public_key)
            && self.metadata_signature.verify(&metadata, public_key)
    }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L17-19)
```rust
// This is the number of unique chunks for which we would track the chunk endorsements.
// Ideally, we should not be processing more than num_shards chunks at a time.
const NUM_CHUNKS_IN_CHUNK_ENDORSEMENTS_CACHE: usize = 100;
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L45-46)
```rust
        let key = endorsement.chunk_production_key();
        let account_id = endorsement.account_id();
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L50-53)
```rust
            if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
                tracing::debug!(target: "client", ?endorsement, "already received chunk endorsement");
                return Ok(());
            }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L60-63)
```rust
                cache.get_or_insert_mut(key, || HashMap::new()).insert(
                    account_id.clone(),
                    (endorsement.chunk_hash(), endorsement.signature()),
                );
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L107-110)
```rust
        let validator_signatures = entry
            .into_iter()
            .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
            .map(|(account_id, (_, signature))| (account_id, signature.clone()))
```

**File:** core/primitives/src/spice/chunk_endorsement.rs (L31-41)
```rust
        let signed_data = SpiceEndorsementSignedData {
            execution_result_hash: execution_result.compute_hash(),
            chunk_id,
        };
        let signature = signer.sign_bytes(&signed_data.serialize_data_for_signing());
        Self::V1(SpiceChunkEndorsementV1 {
            chunk_id: signed_data.chunk_id,
            account_id: signer.validator_id().clone(),
            signature,
            execution_result,
        })
```
