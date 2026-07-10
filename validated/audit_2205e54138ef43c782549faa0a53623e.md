### Title
Unbounded RocksDB Disk Storage Leak in Request Stores — (`File: crates/node/src/storage.rs`)

### Summary

Every MPC node permanently writes every incoming `SignatureRequest`, `CKDRequest`, and `VerifyForeignTransactionRequest` to its local RocksDB database via `SignRequestStorage::add()`, `CKDRequestStorage::add()`, and `VerifyForeignTransactionRequestStorage::add()`. None of these stores expose a `remove()` method, and the `monitor_block_updates` loop that calls them contains an explicit `TODO(#3032)` acknowledging that completed and finalized requests are never deleted. The result is a monotonically growing on-disk resource leak that is structurally identical to the C-string memory leak in the reference report: a resource is allocated on every processed event and never freed.

### Finding Description

`SignRequestStorage`, `CKDRequestStorage`, and `VerifyForeignTransactionRequestStorage` each wrap a `SecretDB` (RocksDB) column and expose only `add()` and `get()`. The `add()` path unconditionally writes a serialized request to the database column and never deletes it. [1](#0-0) 

The call sites in `monitor_block_updates` invoke `add()` for every request observed in every new block: [2](#0-1) 

The two TODO comments at lines 284 and 289 explicitly acknowledge that (a) the stores are not yet unified and (b) completed/finalized requests are never removed. The in-memory `PendingRequests` queue does evict expired and resolved entries from its `HashMap`, but that eviction has no effect on the RocksDB-backed stores. [3](#0-2) 

A parallel, self-documented disk leak already exists for unowned triple/presignature assets: [4](#0-3) 

The request-store leak is distinct and additive: it affects all three request types and is proportional to the total number of on-chain requests ever processed, not just discarded assets.

### Impact Explanation

Every MPC node's RocksDB grows without bound for as long as the network processes requests. A node that has been running for months accumulates every historical `SignatureRequest`, `CKDRequest`, and `VerifyForeignTransactionRequest` in its `DBCol::SignRequest`, `DBCol::CKDRequest`, and `DBCol::VerifyForeignTxRequest` columns. When disk space is exhausted, RocksDB write operations fail with an unrecoverable error (the code calls `.expect("Unrecoverable error writing to database")`), crashing the node process. If enough nodes crash simultaneously, the network falls below the signing threshold and all pending signature requests are permanently stranded — matching the "permanent freezing of funds" and "request-lifecycle invariant break" categories in the allowed impact scope.

### Likelihood Explanation

The leak is triggered by normal, unprivileged usage: any account calling `sign()` on the NEAR MPC contract produces a `SignatureRequest` that is indexed and stored permanently. No special access, collusion, or adversarial behavior is required. The rate of accumulation equals the network's request throughput. On a busy mainnet deployment the disk fills faster; on a lightly loaded testnet it fills slowly but inevitably.

### Recommendation

Add a `remove()` method to each request storage type and call it from `monitor_block_updates` after a request is confirmed resolved or expired by `PendingRequests::get_requests_to_attempt()`. The removal should be gated on finality (i.e., after the response block is canonical) to avoid deleting a request that a follower node still needs to look up. Resolving `TODO(#3032)` is the direct fix.

### Proof of Concept

1. Deploy a local MPC cluster.
2. Submit a continuous stream of `sign()` calls from any NEAR account.
3. Observe `DBCol::SignRequest` in each node's RocksDB growing monotonically — entries are inserted by `SignRequestStorage::add()` and never deleted.
4. After the requests expire (200 blocks), confirm via RocksDB inspection that the rows remain present, while the in-memory `PendingRequests::requests` `HashMap` has evicted them.
5. Extrapolate: at mainnet throughput, disk exhaustion occurs within months, causing `.expect("Unrecoverable error writing to database")` panics and node crashes. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/node/src/storage.rs (L8-71)
```rust
pub struct SignRequestStorage {
    db: Arc<SecretDB>,
    add_sender: broadcast::Sender<SignatureId>,
}

impl SignRequestStorage {
    pub fn new(db: Arc<SecretDB>) -> anyhow::Result<Self> {
        let (tx, _) = tokio::sync::broadcast::channel(500);
        Ok(Self { db, add_sender: tx })
    }

    /// If given request is already in the database, returns false.
    /// Otherwise, inserts the request and returns true.
    pub fn add(&self, request: &SignatureRequest) -> bool {
        let key = borsh::to_vec(&request.id).unwrap();
        if self
            .db
            .get(DBCol::SignRequest, &key)
            .expect("Unrecoverable error reading from database")
            .is_some()
        {
            return false;
        }
        let value_ser = serde_json::to_vec(&request).unwrap();
        let mut update = self.db.update();
        update.put(DBCol::SignRequest, &key, &value_ser);
        update
            .commit()
            .expect("Unrecoverable error writing to database");
        let _ = self.add_sender.send(request.id);
        true
    }

    /// Returns when a signature request with given id is present, then returns it.
    /// This behavior is necessary because a peer might initiate computation for a signature
    /// request before our indexer has caught up to the request. We need proof of the request
    /// from on-chain in order to participate in the computation.
    pub async fn get(&self, id: SignatureId) -> Result<SignatureRequest, anyhow::Error> {
        let key = borsh::to_vec(&id)?;
        let mut rx = self.add_sender.subscribe();
        if let Some(request_ser) = self.db.get(DBCol::SignRequest, &key)? {
            return Ok(serde_json::from_slice(&request_ser)?);
        }
        loop {
            let added_id = match rx.recv().await {
                Ok(added_id) => added_id,
                Err(e) => match e {
                    broadcast::error::RecvError::Closed => {
                        metrics::SIGN_REQUEST_CHANNEL_FAILED.inc();
                        return Err(anyhow::anyhow!("Error in sign_request channel recv, {e}"));
                    }
                    broadcast::error::RecvError::Lagged(msg_n) => {
                        tracing::info!("{msg_n} messages lagged during sign_request channel recv");
                        continue;
                    }
                },
            };
            if added_id == id {
                break;
            }
        }
        let request_ser = self.db.get(DBCol::SignRequest, &key)?.unwrap();
        Ok(serde_json::from_slice(&request_ser)?)
    }
```

**File:** crates/node/src/mpc_client.rs (L284-314)
```rust
                    // TODO(#3031): add batch request and unify stores
                    for request in &signature_requests.requests {
                        self.sign_request_store.add(request);
                    }

                    // TODO(#3032): remove completed & finalized requests from store
                    pending_signatures.notify_new_block(signature_requests);

                    let ckd_requests: RequestsUpdate<CKDRequest> = RequestsUpdate::from_chain(
                        &block_update.block,
                        block_status.clone(),
                        block_update.ckd_requests,
                        block_update.completed_ckds
                    );
                    for request in &ckd_requests.requests {
                        self.ckd_request_store.add(request);
                    }

                    pending_ckds.notify_new_block(ckd_requests);

                    let verify_foreign_tx_requests : RequestsUpdate<VerifyForeignTxRequest> = RequestsUpdate::from_chain(
                        &block_update.block,
                        block_status,
                        block_update.verify_foreign_tx_requests,
                        block_update.completed_verify_foreign_txs
                    );

                    for request in &verify_foreign_tx_requests.requests {
                        self.verify_foreign_tx_request_store.add(request);
                    }
                    pending_verify_foreign_txs.notify_new_block(verify_foreign_tx_requests);
```

**File:** crates/node/src/requests/queue.rs (L65-67)
```rust
    /// Map from request ID to the request. Successful and expired requests are removed
    /// from this map. This is the "queue".
    pub(super) requests: HashMap<RequestId, QueuedRequest<RequestType, ChainRespondArgsType>>,
```

**File:** docs/asset-generation.md (L199-209)
```markdown
**Note on orphaned unowned assets:** When an owned asset is discarded, only the
local copy is deleted. There is no mechanism to notify borrower nodes to delete
their unowned copies of the same asset. Since `take_unowned(id)` is never called
for a discarded asset, those copies remain in borrowers' `RocksDB` indefinitely.
`clean_db()` cannot find them either — it only iterates keys namespaced by
`my_participant_id`, while unowned assets are keyed by the original owner's
participant ID. This also means the same-epoch TLS-key-change cleanup
(`KeepOnly` branch in `delete_stale_triples_and_presignatures()`) misses
unowned assets, since it delegates to `clean_db()`. The only event that
clears them is a full asset wipe on epoch change (resharing). In normal
operation this constitutes a slow disk storage leak.
```
