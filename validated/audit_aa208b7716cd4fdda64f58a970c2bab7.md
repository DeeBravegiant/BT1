### Title
Untyped Data Signing in `SnapshotHostInfo` Enables Cross-Context Signature Replay - (File: chain/network/src/network_protocol/state_sync.rs)

### Summary

`SnapshotHostInfo::build_hash` signs the tuple `(sync_hash, epoch_height, shards)` with no type/domain tag. Every other signed message in the codebase that uses the same validator/node key includes a `SignatureDifferentiator` or equivalent tag. The missing tag allows a valid signature produced by a peer's node key in any other context whose borsh encoding collides with `(CryptoHash, u64, Vec<u64>)` to be replayed as a legitimate `SnapshotHostInfo`, causing the `SnapshotHostsCache` to accept a forged entry and redirect state-sync requests to a peer that does not hold the snapshot.

### Finding Description

`SnapshotHostInfo::build_hash` computes the signed digest as:

```rust
CryptoHash::hash_borsh((sync_hash, epoch_height, shards))
``` [1](#0-0) 

No type prefix, no `SignatureDifferentiator`, and no `peer_id` binding is included in the signed payload. The signature is then verified only against `self.peer_id.public_key()`:

```rust
if !self.signature.verify(self.hash().as_ref(), self.peer_id.public_key()) {
``` [2](#0-1) 

Every other signing path in the codebase that uses the same key type includes a differentiator. For example, `SpiceEndorsementSignedData::serialize_data_for_signing` prepends `"SpiceChunkEndorsement"`:

```rust
static SIGNATURE_DIFFERENTIATOR: StaticSignatureDifferentiator = "SpiceChunkEndorsement";
let data_for_signing = (self, SIGNATURE_DIFFERENTIATOR);
borsh::to_vec(&data_for_signing).unwrap()
``` [3](#0-2) 

`PartialEncodedStateWitnessInner` embeds a `signature_differentiator` field directly in the struct: [4](#0-3) 

`OptimisticBlockInner` does the same: [5](#0-4) 

The `AccountKeyPayload` proto wrapper uses a `oneof` discriminant to prevent cross-message replay: [6](#0-5) 

`SnapshotHostInfo` has none of these protections.

Additionally, `peer_id` is not included in the signed data. The borsh encoding of `(CryptoHash [32 B], u64 [8 B], Vec<u64>)` is a generic layout that could collide with other signed messages using the same node key.

### Impact Explanation

A forged `SnapshotHostInfo` that passes `verify()` is inserted into `SnapshotHostsCache` by `SnapshotHostsCache::insert`: [7](#0-6) 

The cache is the sole source of peer selection for state sync. A corrupted cache entry causes state-sync requests to be routed to a peer that does not hold the snapshot, making state sync fail for any node that selects that peer. The corrupted DB entry is the `SnapshotHostsCache` record keyed by `peer_id`.

### Likelihood Explanation

The borsh layout of `(CryptoHash, u64, Vec<u64>)` is generic. Any other protocol message signed with the same node key whose borsh encoding matches this layout — including future messages — can be replayed as a `SnapshotHostInfo`. Because `peer_id` is not bound in the signed data, an attacker who observes two peers that have signed the same `(sync_hash, epoch_height, shards)` tuple (e.g., both hosting the same snapshot) can swap their signatures, attributing peer A's signature to peer B's `SnapshotHostInfo`. The `SyncSnapshotHosts` message is accepted from any connected peer with no privilege requirement. [8](#0-7) 

### Recommendation

Add a `StaticSignatureDifferentiator` to the signed payload in `build_hash`, consistent with every other signing path in the codebase:

```rust
fn build_hash(
    sync_hash: &CryptoHash,
    epoch_height: &EpochHeight,
    shards: &Vec<ShardId>,
) -> CryptoHash {
    static DIFFERENTIATOR: StaticSignatureDifferentiator = "SnapshotHostInfo";
    CryptoHash::hash_borsh((sync_hash, epoch_height, shards, DIFFERENTIATOR))
}
```

Also bind `peer_id` inside the signed data so the signature is tied to a specific peer identity.

### Proof of Concept

1. Peer A (node key `K_A`) signs `SnapshotHostInfo` with `(sync_hash=X, epoch_height=Y, shards=Z)` and broadcasts it.
2. Peer B (node key `K_B`) also signs `(sync_hash=X, epoch_height=Y, shards=Z)` (same snapshot, same epoch).
3. An attacker observing both messages constructs a new `SnapshotHostInfo` with `peer_id=A` but attaches B's signature — or vice versa — and sends it to a victim node.
4. `SnapshotHostInfo::verify()` checks `self.signature.verify(self.hash().as_ref(), self.peer_id.public_key())`. Since `hash()` only covers `(sync_hash, epoch_height, shards)` and both peers signed the same tuple, the swapped signature verifies successfully against the wrong `peer_id`.
5. The victim's `SnapshotHostsCache` now records that peer A is hosting shards it does not actually hold, and state-sync requests to peer A fail. [9](#0-8)

### Citations

**File:** chain/network/src/network_protocol/state_sync.rs (L40-79)
```rust
impl SnapshotHostInfo {
    fn build_hash(
        sync_hash: &CryptoHash,
        epoch_height: &EpochHeight,
        shards: &Vec<ShardId>,
    ) -> CryptoHash {
        CryptoHash::hash_borsh((sync_hash, epoch_height, shards))
    }

    pub(crate) fn new(
        peer_id: PeerId,
        sync_hash: CryptoHash,
        epoch_height: EpochHeight,
        shards: Vec<ShardId>,
        secret_key: &SecretKey,
    ) -> Self {
        #[cfg(not(test))]
        assert_eq!(&secret_key.public_key(), peer_id.public_key());
        let hash = Self::build_hash(&sync_hash, &epoch_height, &shards);
        let signature = secret_key.sign(hash.as_ref());
        Self { peer_id, sync_hash, epoch_height, shards, signature }
    }

    pub(crate) fn hash(&self) -> CryptoHash {
        Self::build_hash(&self.sync_hash, &self.epoch_height, &self.shards)
    }

    pub(crate) fn verify(&self) -> Result<(), SnapshotHostInfoVerificationError> {
        // Number of shards must be limited, otherwise it'd be possible to create malicious
        // messages with millions of shard ids.
        if self.shards.len() > MAX_SHARDS_PER_SNAPSHOT_HOST_INFO {
            return Err(SnapshotHostInfoVerificationError::TooManyShards(self.shards.len()));
        }

        if !self.signature.verify(self.hash().as_ref(), self.peer_id.public_key()) {
            return Err(SnapshotHostInfoVerificationError::InvalidSignature);
        }

        Ok(())
    }
```

**File:** core/primitives/src/spice/chunk_endorsement.rs (L176-180)
```rust
    fn serialize_data_for_signing(&self) -> Vec<u8> {
        static SIGNATURE_DIFFERENTIATOR: StaticSignatureDifferentiator = "SpiceChunkEndorsement";
        let data_for_signing = (self, SIGNATURE_DIFFERENTIATOR);
        borsh::to_vec(&data_for_signing).unwrap()
    }
```

**File:** core/primitives/src/stateless_validation/partial_witness.rs (L101-120)
```rust
    signature_differentiator: SignatureDifferentiator,
}

impl PartialEncodedStateWitnessInner {
    fn new(
        epoch_id: EpochId,
        chunk_header: ShardChunkHeader,
        part_ord: usize,
        part: Vec<u8>,
        encoded_length: usize,
    ) -> Self {
        Self {
            epoch_id,
            shard_id: chunk_header.shard_id(),
            height_created: chunk_header.height_created(),
            part_ord,
            part: part.into_boxed_slice(),
            encoded_length,
            signature_differentiator: "PartialEncodedStateWitness".to_owned(),
        }
```

**File:** core/primitives/src/optimistic_block.rs (L16-26)
```rust
#[derive(BorshSerialize, BorshDeserialize, Clone, Debug, Eq, PartialEq, ProtocolSchema)]
pub struct OptimisticBlockInner {
    pub prev_block_hash: CryptoHash,
    pub block_height: BlockHeight,
    pub block_timestamp: u64,
    // Data to confirm the correctness of randomness beacon output
    pub random_value: CryptoHash,
    pub vrf_value: near_crypto::vrf::Value,
    pub vrf_proof: near_crypto::vrf::Proof,
    signature_differentiator: SignatureDifferentiator,
}
```

**File:** chain/network/src/network_protocol/network.proto (L24-40)
```text
// A payload that can be signed with account keys.
// Since account keys are used to sign things in independent contexts,
// we need this common enum to prevent message replay attacks, like this one:
// - messages M1 and M2 of different types happen to have the same serialized representation.
// - an attacker observes M1 signed by A in some context
// - the attacker then sends M2 with A's signature of M1 (which also matches M2, since
//   their serialized representations match) to B, effectively impersonating A.
// NOTE: that proto serialization is non-unique, so the message passed around with the signature
// should be in serialized form.
// TODO: move to a separate file, probably in a separate package.
message AccountKeyPayload {
  reserved 1;
  oneof payload_type {
    AccountData account_data = 2;
    OwnedAccount owned_account = 3;
  }
}
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L376-389)
```rust
    pub async fn insert(
        self: &Self,
        data: Vec<Arc<SnapshotHostInfo>>,
    ) -> (Vec<Arc<SnapshotHostInfo>>, Option<SnapshotHostInfoError>) {
        // Execute verification on the rayon threadpool.
        let (data, err) = self.verify(data).await;
        if data.is_empty() {
            return (vec![], err);
        }
        // Insert the successfully verified data.
        let mut inner = self.0.lock();
        data.iter().for_each(|d| inner.insert(d));
        (data, err)
    }
```

**File:** chain/network/src/peer/peer_actor.rs (L1237-1261)
```rust
            PeerMessage::SyncSnapshotHosts(msg) => {
                metrics::SYNC_SNAPSHOT_HOSTS.with_label_values(&["received"]).inc();
                // Early exit, if there is no data in the message.
                if msg.hosts.is_empty() {
                    #[cfg(test)]
                    message_processed_event();
                    return;
                }
                let network_state = self.network_state.clone();
                let tcp = self.tcp.clone();
                self.handle.spawn("handle sync snapshot hosts", async move {
                    if let Some(err) = network_state.add_snapshot_hosts(msg.hosts, tcp).await {
                        conn.stop(Some(match err {
                            SnapshotHostInfoError::VerificationError(
                                SnapshotHostInfoVerificationError::InvalidSignature,
                            ) => ReasonForBan::InvalidSignature,
                            SnapshotHostInfoError::VerificationError(
                                SnapshotHostInfoVerificationError::TooManyShards(_),
                            )
                            | SnapshotHostInfoError::DuplicatePeerId => ReasonForBan::Abusive,
                        }));
                    }
                    #[cfg(test)]
                    message_processed_event();
                });
```
