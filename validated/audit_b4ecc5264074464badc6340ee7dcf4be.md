The code is clear. Let me verify the storage write path to confirm the corrupted hash reaches persistent storage.

### Title
Unauthenticated P2P Peer Can Inject Arbitrary Transaction Hashes Into Node Storage, Corrupting RPC Responses - (`crates/apollo_p2p_sync/src/client/transaction.rs`)

---

### Summary

`TransactionStreamFactory::parse_data_for_block` accepts the `transaction_hash` field of a peer-supplied `FullTransaction` verbatim and stores it in persistent storage without ever verifying it matches the hash of the accompanying `Transaction` body. Any unauthenticated SQMR responder peer can supply a `FullTransaction` with an arbitrary `transaction_hash` (e.g., `Felt::ONE`) and a mismatched body, causing the syncing node to permanently store the wrong hash in its `transaction_hash_to_idx` and `transaction_metadata` tables. Subsequent RPC calls on that node (`starknet_getTransactionByHash`, `starknet_traceTransaction`, `starknet_simulateTransactions`) will either fail to find the real transaction or return authoritative-looking wrong data keyed to the fake hash.

---

### Finding Description

In `parse_data_for_block`, the loop at lines 67–91 reads each `FullTransaction` from the network response manager and pushes all three fields directly into `block_body`:

```rust
block_body.transactions.push(transaction);
block_body.transaction_outputs.push(transaction_output);
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);   // ← peer-controlled value, no check
``` [1](#0-0) 

The TODO comment is a developer acknowledgment that this validation is missing. No call to `validate_transaction_hash` (which exists in `crates/starknet_api/src/transaction_hash.rs`) is made anywhere in this path. [2](#0-1) 

The resulting `BlockBody` is then written to storage unconditionally via `write_to_storage` → `append_body`:

```rust
storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
``` [3](#0-2) 

Inside `write_transactions`, the peer-supplied `tx_hash` is inserted into two persistent tables:

```rust
transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
transaction_metadata_table.append(txn, &transaction_index,
    &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash })?;
``` [4](#0-3) 

The real transaction hash is never indexed. The fake hash is indexed in its place.

---

### Impact Explanation

**High.** The RPC layer uses `get_transaction_idx_by_hash` to resolve transaction lookups:

```rust
let TransactionIndex(block_number, tx_offset) = storage_txn
    .get_transaction_idx_by_hash(&transaction_hash)
    ...
    .ok_or(TRANSACTION_HASH_NOT_FOUND)?;
``` [5](#0-4) 

With the corrupted storage:
- `starknet_getTransactionByHash(real_hash)` → `TRANSACTION_HASH_NOT_FOUND` (real hash not in index)
- `starknet_getTransactionByHash(fake_hash)` → returns the wrong transaction body
- `starknet_traceTransaction` and `starknet_simulateTransactions` use `get_block_transaction_hashes`, which reads from `transaction_metadata` and returns the fake hash vector, producing authoritative-looking wrong trace/simulation results [6](#0-5) 

This fits the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."** and **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

- The attacker only needs to be selected as the SQMR responder for a `TransactionQuery` — no authentication, no credentials, no privileged position required.
- The precondition (a stored header with `n_transactions >= 1`) is trivially satisfied on any non-empty chain.
- The attack is permanent: once committed, the corrupted hash survives node restarts and is not corrected by any subsequent sync step.
- The developer TODO at line 88 confirms this is a known-missing guard, not an intentional design choice.

---

### Recommendation

Before pushing `transaction_hash` into `block_body.transaction_hashes`, call `validate_transaction_hash` (or `get_transaction_hash`) to verify the peer-supplied hash matches the hash computed from the `transaction` body and the node's `chain_id`. On mismatch, return `ParseDataError::BadPeer` (consistent with how other peer data errors are handled in this file) and report the peer.

---

### Proof of Concept

```rust
// Inject a FullTransaction with transaction_hash = Felt::ONE
// and a mismatched InvokeTransaction body into the mock SQMR responder.
// Drive parse_data_for_block to completion (target_transaction_len == 1).
// Assert:
//   get_block_transaction_hashes(block_number) == [Felt::ONE]   // stored fake hash
//   get_transaction_hash(&invoke_body, chain_id) != Felt::ONE   // real hash differs
//   get_transaction_idx_by_hash(real_hash) == None              // real hash not indexed
//   get_transaction_idx_by_hash(Felt::ONE) == Some(index)       // fake hash indexed
``` [7](#0-6)

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L54-95)
```rust
    fn parse_data_for_block<'a>(
        transactions_response_manager: &'a mut ClientResponsesManager<DataOrFin<FullTransaction>>,
        block_number: BlockNumber,
        storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            let mut block_body = BlockBody::default();
            let mut current_transaction_len = 0;
            let target_transaction_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .n_transactions;
            while current_transaction_len < target_transaction_len {
                let maybe_transaction = transactions_response_manager.next().await.ok_or(
                    ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }),
                )?;
                let Some(FullTransaction { transaction, transaction_output, transaction_hash }) =
                    maybe_transaction?.0
                else {
                    if current_transaction_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::NotEnoughTransactions {
                            expected: target_transaction_len,
                            actual: current_transaction_len,
                            block_number: block_number.0,
                        }));
                    }
                };
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
            }
            Ok(Some((block_body, block_number)))
        }
        .boxed()
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

**File:** crates/apollo_storage/src/body/mod.rs (L622-627)
```rust
        transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
        transaction_metadata_table.append(
            txn,
            &transaction_index,
            &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
        )?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1214-1217)
```rust
            let TransactionIndex(block_number, tx_offset) = storage_txn
                .get_transaction_idx_by_hash(&transaction_hash)
                .map_err(internal_server_error)?
                .ok_or(TRANSACTION_HASH_NOT_FOUND)?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1228-1235)
```rust
            let transaction_hashes = storage_txn
                .get_block_transaction_hashes(block_number)
                .map_err(internal_server_error)?
                .ok_or_else(|| {
                    internal_server_error(StorageError::DBInconsistency {
                        msg: format!("Missing block {block_number} transactions"),
                    })
                })?;
```
