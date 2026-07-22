### Title
Unauthenticated P2P Peer Can Inject Fabricated `TransactionOutput` (Including Fake `execution_status`) Into Node Storage — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary

`TransactionStreamFactory::parse_data_for_block` accepts `transaction_output` from unauthenticated p2p peers and stores it directly via `append_body` without verifying it against the `receipt_commitment` already stored in the block header. A malicious peer can fabricate any `execution_status` (e.g., `Reverted` with an arbitrary `revert_reason`) for a canonically-succeeded transaction, and the node will persist and serve the corrupted receipt via `starknet_getTransactionReceipt`.

### Finding Description

In `parse_data_for_block`, the node receives `FullTransaction { transaction, transaction_output, transaction_hash }` from a p2p peer and pushes `transaction_output` directly into `block_body.transaction_outputs` with no integrity check: [1](#0-0) 

The only validation performed is a count check against `n_transactions` from the stored header. There is no verification of `transaction_output` against the `receipt_commitment` field that is already stored in the block header: [2](#0-1) 

The `receipt_commitment` is a Poseidon Merkle root over all receipt hashes (which include `execution_status`, `actual_fee`, `messages_sent`, `revert_reason`, and `execution_resources`): [3](#0-2) 

The fabricated body is then committed to storage unconditionally: [4](#0-3) 

`write_transactions` stores the peer-supplied `tx_output` verbatim: [5](#0-4) 

The RPC handler `get_transaction_receipt` reads directly from this storage with no re-validation: [6](#0-5) 

A developer TODO on line 88 explicitly acknowledges that data from untrusted sources is not validated: [7](#0-6) 

### Impact Explanation

Any node syncing via p2p will store and serve fabricated transaction receipts. `starknet_getTransactionReceipt` will return an attacker-controlled `execution_status`, `revert_reason`, `actual_fee`, `messages_sent`, and `execution_resources` for any transaction in any synced block. This is a **High** impact: the RPC returns an authoritative-looking wrong value. The "Critical fee/refund accounting" claim in the question is overstated — syncing nodes do not re-execute transactions, so on-chain fee accounting is unaffected. The corruption is limited to the node's stored view and its RPC responses.

### Likelihood Explanation

Any peer the syncing node connects to can trigger this. P2P peers are unauthenticated. The attacker only needs to respond to a transaction query with a well-formed `FullTransaction` protobuf message containing a fabricated `Receipt`. No special privileges are required.

### Recommendation

After storing the block body, compute the Poseidon receipt commitment over the stored `transaction_outputs` and compare it against `block_header.receipt_commitment`. Reject and report the peer if the commitment does not match. The infrastructure for computing this commitment already exists in `calculate_receipt_commitment`: [3](#0-2) 

### Proof of Concept

1. Intercept or act as a p2p peer responding to a transaction sync query for block N.
2. Return a valid `FullTransaction` for each transaction in the block, but set `transaction_output.execution_status = Reverted { revert_reason: "fabricated" }` for a transaction that canonically succeeded.
3. The syncing node stores the body without complaint.
4. Call `starknet_getTransactionReceipt` with the transaction hash — the response will show `execution_status: REVERTED` and `revert_reason: "fabricated"` instead of `SUCCEEDED`.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-89)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
```

**File:** crates/starknet_api/src/block.rs (L224-226)
```rust
    pub n_events: usize,
    #[serde(skip_serializing)]
    pub receipt_commitment: Option<ReceiptCommitment>,
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L315-316)
```rust
    let receipt_elements: Vec<ReceiptElement> =
        transactions_data.iter().map(ReceiptElement::from).collect();
```

**File:** crates/apollo_storage/src/body/mod.rs (L619-620)
```rust
        let tx_location = file_handlers.append_transaction(tx);
        let tx_output_location = file_handlers.append_transaction_output(tx_output);
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L537-569)
```rust
    async fn get_transaction_receipt(
        &self,
        transaction_hash: TransactionHash,
    ) -> RpcResult<GeneralTransactionReceipt> {
        verify_storage_scope(&self.storage_reader)?;

        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        if let Some(transaction_index) =
            txn.get_transaction_idx_by_hash(&transaction_hash).map_err(internal_server_error)?
        {
            let tx = txn
                .get_transaction(transaction_index)
                .map_err(internal_server_error)?
                .unwrap_or_else(|| panic!("Should have tx {transaction_hash}"));

            // TODO(Shahak): Add version function to transaction in SN_API.
            let tx_version = match &tx {
                StarknetApiTransaction::Declare(tx) => tx.version(),
                StarknetApiTransaction::Deploy(tx) => tx.version,
                StarknetApiTransaction::DeployAccount(tx) => tx.version(),
                StarknetApiTransaction::Invoke(tx) => tx.version(),
                StarknetApiTransaction::L1Handler(tx) => tx.version,
            };

            let msg_hash = match tx {
                StarknetApiTransaction::L1Handler(l1_handler_tx) => {
                    Some(l1_handler_tx.calc_msg_hash())
                }
                _ => None,
            };

            get_non_pending_receipt(&txn, transaction_index, transaction_hash, tx_version, msg_hash)
```
