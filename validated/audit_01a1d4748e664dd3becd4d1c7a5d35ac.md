### Title
P2P Peer Can Inject Fake `revert_reason` to Corrupt `TransactionExecutionStatus` Stored and Served by RPC — (`crates/apollo_protobuf/src/converters/receipt.rs`)

### Summary

`parse_common_receipt_fields` unconditionally maps a non-`None` `revert_reason` field in a protobuf receipt to `TransactionExecutionStatus::Reverted`. Because the p2p sync client accepts `FullTransaction` data from unauthenticated peers and writes it directly to storage with no cross-validation, a malicious peer can set `revert_reason = Some("fake revert")` on any succeeded transaction, causing the node to permanently store and serve a wrong `execution_status` via `starknet_getTransactionReceipt`.

### Finding Description

In `parse_common_receipt_fields`: [1](#0-0) 

The sole signal used to determine `TransactionExecutionStatus` is the presence of `revert_reason` in the protobuf `Common` message. There is no independent check against the actual execution outcome, the block commitment, or any other canonical source. Any peer that sets `revert_reason = Some(arbitrary_string)` will produce `TransactionExecutionStatus::Reverted(...)` regardless of whether the transaction actually reverted.

The p2p sync client receives `FullTransaction` objects (which include the `TransactionOutput` containing `execution_status`) and pushes them directly into `BlockBody::transaction_outputs` with no validation: [2](#0-1) 

The comment on line 88 (`// TODO(eitan): Validate transaction hash from untrusted sources`) confirms that the codebase itself acknowledges the absence of validation for peer-supplied data. The assembled `BlockBody` is then written to storage unconditionally: [3](#0-2) 

Storage writes the `tx_output` (including the corrupted `execution_status`) verbatim: [4](#0-3) 

The RPC handler for `starknet_getTransactionReceipt` reads this stored output and returns it as an authoritative response: [5](#0-4) 

### Impact Explanation

A node syncing via p2p from a malicious peer will permanently store `TransactionExecutionStatus::Reverted` for transactions that actually succeeded. Every subsequent call to `starknet_getTransactionReceipt` or `starknet_getTransactionStatus` on that node will return `"execution_status": "REVERTED"` with an attacker-controlled `revert_reason` string for those transactions. Clients relying on this node (wallets, bridges, indexers) will incorrectly treat successful transactions as failed, potentially triggering double-spend retries, incorrect fund accounting, or bridge logic errors.

### Likelihood Explanation

Any node operator who configures their node to sync from p2p peers (the standard full-node sync mode) is exposed. The attacker only needs to operate a p2p peer that the victim node connects to. No privileged access is required. The protobuf field is a plain optional string with no authentication or commitment binding.

### Recommendation

In `parse_common_receipt_fields`, the `execution_status` must not be derived solely from the presence of `revert_reason`. The correct fix is to add an explicit `execution_status` field to the protobuf `Common` message (matching the Starknet p2p spec's `ExecutionStatus` enum) and validate that `revert_reason` is `Some` if and only if `execution_status == REVERTED`. Additionally, the p2p sync client should validate received `TransactionOutput` fields against the block's receipt commitment (already stored in the header) before writing to storage.

### Proof of Concept

```rust
// In crates/apollo_protobuf/src/converters/receipt.rs (or a test file)
#[test]
fn fake_revert_reason_corrupts_execution_status() {
    use crate::protobuf;
    use starknet_api::transaction::TransactionExecutionStatus;

    // Construct a protobuf Common with revert_reason set, simulating a malicious peer
    let common = protobuf::receipt::Common {
        actual_fee: Some(protobuf::Felt252 { elements: vec![0u8; 32] }),
        price_unit: 0,
        messages_sent: vec![],
        execution_resources: Some(protobuf::receipt::ExecutionResources {
            builtins: Some(protobuf::receipt::execution_resources::BuiltinCounter::default()),
            steps: 0,
            memory_holes: 0,
            gas_consumed: Some(Default::default()),
            da_gas_consumed: Some(Default::default()),
        }),
        revert_reason: Some("fake revert".to_string()), // attacker-controlled
    };

    let (_, _, execution_status, _) = parse_common_receipt_fields(Some(common)).unwrap();

    // This assertion passes — a succeeded tx is now marked Reverted
    assert!(matches!(
        execution_status,
        TransactionExecutionStatus::Reverted(r) if r.revert_reason == "fake revert"
    ));
}
```

The test demonstrates that `parse_common_receipt_fields` produces `Reverted` purely from the peer-supplied string, with no validation against actual execution outcome.

### Citations

**File:** crates/apollo_protobuf/src/converters/receipt.rs (L429-434)
```rust
    let execution_status =
        common.revert_reason.map_or(TransactionExecutionStatus::Succeeded, |revert_reason| {
            TransactionExecutionStatus::Reverted(RevertedTransactionExecutionStatus {
                revert_reason,
            })
        });
```

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

**File:** crates/apollo_storage/src/body/mod.rs (L619-620)
```rust
        let tx_location = file_handlers.append_transaction(tx);
        let tx_output_location = file_handlers.append_transaction_output(tx_output);
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L569-569)
```rust
            get_non_pending_receipt(&txn, transaction_index, transaction_hash, tx_version, msg_hash)
```
