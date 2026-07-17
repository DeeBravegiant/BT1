### Title
Cross-Endorsement Mix-and-Match Cache Poisoning via Unbound `inner`/`metadata` Signatures in `ChunkEndorsementV2::verify` — (`core/primitives/src/stateless_validation/chunk_endorsement.rs`)

---

### Summary

`ChunkEndorsementV2` signs `inner` (containing `chunk_hash`) and `metadata` (containing `shard_id`, `epoch_id`, `height_created`) as two **independent** byte blobs with no cryptographic cross-binding. A malicious peer who has observed two legitimate endorsements from the same validator — one for chunk A and one for chunk B — can splice them into a single forged message that passes all signature checks, poisons the block producer's endorsement cache under chunk B's key with chunk A's hash, and causes the dedup guard to permanently drop the real chunk B endorsement from that validator.

---

### Finding Description

**Root cause — `ChunkEndorsementV2::verify` (no cross-binding)** [1](#0-0) 

`inner` is serialized and verified independently; `metadata` is serialized and verified independently. Nothing in either signed payload references the other. A message where `inner` comes from endorsement-A and `metadata` comes from endorsement-B will satisfy both checks as long as both were signed by the same key.

**Construction of the forged message**

A validator V assigned to both chunk A and chunk B produces two legitimate endorsements that are broadcast over P2P:

```
E_A = { inner_A(chunk_A_hash), sig_A, metadata_A(shard_A, epoch_A, h_A), meta_sig_A }
E_B = { inner_B(chunk_B_hash), sig_B, metadata_B(shard_B, epoch_B, h_B), meta_sig_B }
```

The attacker constructs:

```
E_forged = { inner_A, sig_A, metadata_B, meta_sig_B }
```

`sig_A` is a valid signature over `inner_A` by V; `meta_sig_B` is a valid signature over `metadata_B` by V. `ChunkEndorsementV2::verify(V.pubkey)` returns `true`.

**Validation path — all guards pass** [2](#0-1) 

- `chunk_production_key()` is derived from `metadata_B` → chunk B's `(shard_B, epoch_B, h_B)`.
- `validate_chunk_relevant_as_validator` checks V is a chunk validator for chunk B's key → passes (V is legitimately assigned).
- `validate_chunk_endorsement_signature` calls `endorsement.verify(V.pubkey)` → passes (both sigs independently valid).
- Returns `ChunkRelevance::Relevant`.

**Cache poisoning** [3](#0-2) 

The forged endorsement is stored under key = chunk B's `ChunkProductionKey`, with value `(chunk_A_hash, sig_A)`.

**Dedup guard permanently drops the real endorsement** [4](#0-3) 

When the real `E_B` from V arrives, `cache.peek(&chunk_B_key).is_some_and(|entry| entry.contains_key(V.account_id))` is `true`, so `process_chunk_endorsement` returns `Ok(())` immediately without updating the stored value.

**`collect_chunk_endorsements` excludes V** [5](#0-4) 

The filter `chunk_hash == chunk_header.chunk_hash()` compares `chunk_A_hash` against `chunk_B_hash` → false. V's stake is not counted toward chunk B's endorsement threshold.

---

### Impact Explanation

A malicious peer can suppress any targeted validator's endorsement for a specific chunk by racing the forged message to the block producer before the real endorsement arrives. If enough validators are targeted (enough stake to push the chunk below the endorsement threshold), the block producer cannot collect sufficient endorsements to include chunk B in a block, causing a liveness failure for that chunk. The attack requires no validator key material — only the ability to observe P2P endorsement messages and relay a crafted splice.

---

### Likelihood Explanation

The precondition is that the targeted validator has endorsed at least two different chunks (normal behavior across shards or heights), and that the attacker can observe both endorsements on the P2P network (standard peer connectivity). The timing requirement (deliver forged message before real one) is achievable by a well-connected peer. No privileged access is required.

---

### Recommendation

Cryptographically bind `inner` and `metadata` so they cannot be mixed across endorsements. The simplest fix is to include the `metadata` (or its hash) inside the `inner` signed payload, or to produce a single signature over the concatenation/hash of both. For example, `ChunkEndorsementInnerV1` could include `shard_id`, `epoch_id`, and `height_created` directly, making a single signature cover all identifying fields simultaneously and eliminating the independent-signature surface.

---

### Proof of Concept

```rust
// Attacker observes two legitimate endorsements from validator V:
let e_a = /* V's endorsement for chunk A, shard 0, height 10 */;
let e_b = /* V's endorsement for chunk B, shard 0, height 11 */;

// Splice: inner from E_A, metadata+meta_sig from E_B
let forged = ChunkEndorsement::V2(ChunkEndorsementV2 {
    inner: e_a.inner.clone(),           // chunk_A_hash
    signature: e_a.signature.clone(),   // valid sig over inner_A
    metadata: e_b.metadata.clone(),     // chunk B's key
    metadata_signature: e_b.metadata_signature.clone(), // valid sig over metadata_B
});

// Both signature checks pass:
assert!(forged.verify(V.public_key()));

// Send forged to block producer before real E_B arrives.
// Block producer stores: chunk_B_key -> { V -> (chunk_A_hash, sig_A) }
tracker.process_chunk_endorsement(&forged).unwrap();

// Real E_B arrives — dropped by dedup:
tracker.process_chunk_endorsement(&e_b).unwrap(); // silently dropped

// collect_chunk_endorsements for chunk B: V's entry filtered out (wrong hash)
let state = tracker.collect_chunk_endorsements(&chunk_b_header).unwrap();
assert!(!state.validator_signatures.contains_key(V.account_id())); // V excluded
```

### Citations

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L111-117)
```rust
impl ChunkEndorsementV2 {
    fn verify(&self, public_key: &PublicKey) -> bool {
        let inner = borsh::to_vec(&self.inner).unwrap();
        let metadata = borsh::to_vec(&self.metadata).unwrap();
        self.signature.verify(&inner, public_key)
            && self.metadata_signature.verify(&metadata, public_key)
    }
```

**File:** chain/client/src/stateless_validation/validate.rs (L308-331)
```rust
pub fn validate_chunk_endorsement(
    epoch_manager: &dyn EpochManagerAdapter,
    endorsement: &ChunkEndorsement,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let _span = tracing::debug_span!(
        target: "stateless_validation",
        "validate_chunk_endorsement",
        height = endorsement.chunk_production_key().height_created,
        shard_id = %endorsement.chunk_production_key().shard_id,
        validator = %endorsement.account_id(),
        tag_block_production = true
    )
    .entered();

    require_relevant!(validate_chunk_relevant_as_validator(
        epoch_manager,
        &endorsement.chunk_production_key(),
        endorsement.account_id(),
        store,
    )?);
    validate_chunk_endorsement_signature(epoch_manager, endorsement)?;

    Ok(ChunkRelevance::Relevant)
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L48-54)
```rust
        {
            let cache = self.chunk_endorsements.lock();
            if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
                tracing::debug!(target: "client", ?endorsement, "already received chunk endorsement");
                return Ok(());
            }
        }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L58-63)
```rust
            ChunkRelevance::Relevant => {
                let mut cache = self.chunk_endorsements.lock();
                cache.get_or_insert_mut(key, || HashMap::new()).insert(
                    account_id.clone(),
                    (endorsement.chunk_hash(), endorsement.signature()),
                );
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L105-111)
```rust
        let mut cache = self.chunk_endorsements.lock();
        let entry = cache.get_or_insert(key, || HashMap::new());
        let validator_signatures = entry
            .into_iter()
            .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
            .map(|(account_id, (_, signature))| (account_id, signature.clone()))
            .collect();
```
