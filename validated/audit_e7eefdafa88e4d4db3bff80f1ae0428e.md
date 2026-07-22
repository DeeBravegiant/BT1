### Title
Hardcoded `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler` Silently Corrupts `paid_fee_on_l1` on Every Validator Node — (File: `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

When a validator node receives an L1Handler transaction through the consensus P2P stream and converts it to its internal executable form, the `TransactionConverter` unconditionally injects a hardcoded sentinel `Fee(1)` for `paid_fee_on_l1` instead of the actual fee the sender paid on L1. This is the direct Sequencer analog of the Ethereum `transfer`-with-fixed-stipend bug: a legacy placeholder value is used in place of the real, dynamically-supplied value, causing every validator node to store and execute L1Handler transactions with a fabricated fee.

---

### Finding Description

**Root cause — `convert_consensus_l1_handler_to_internal_l1_handler`:** [1](#0-0) 

The function is called from `convert_consensus_tx_to_internal_consensus_tx` for every `ConsensusTransaction::L1Handler` received over the wire: [2](#0-1) 

**Why the real fee is lost in transit:**

The protobuf wire format for an L1Handler transaction (`L1HandlerV0`) carries only `nonce`, `address`, `entry_point_selector`, and `calldata`. The `paid_fee_on_l1` field is never serialised into the consensus message: [3](#0-2) 

So when the validator deserialises the message and calls `convert_consensus_l1_handler_to_internal_l1_handler`, there is no real fee to recover — and the code substitutes `Fee(1)` with a TODO acknowledging the placeholder.

**Proposer vs. validator divergence:**

The proposer obtains L1Handler transactions from the L1 events scraper, which records the actual ETH value sent with the `LogMessageToL2` event. The proposer executes and stores those transactions with the real `paid_fee_on_l1`. The validator, however, always stores and executes them with `Fee(1)`.

**Blockifier fee check on the validator:**

After execution, `execute_raw` performs the only fee adequacy check: [4](#0-3) 

Because `Fee(1) != Fee(0)`, this check always passes on the validator regardless of what was actually paid on L1. The comment explicitly states "assert only that any amount of fee was paid" — the hardcoded `Fee(1)` trivially satisfies that invariant.

**Corrupted Cende blob field:**

`CentralL1HandlerTransaction` serialises `paid_fee_on_l1` into the blob written to Aerospike for the prover: [5](#0-4) 

The blob is written by the proposer (which has the real fee), so the prover input is correct from the proposer's perspective. However, the `echonet/os_input_builder.py` reads `paid_fee_on_l1` from the central blob: [6](#0-5) 

Any re-execution path that reads from a validator node's storage (e.g., re-execution, sync, or RPC trace) will see `paid_fee_on_l1 = 1` for every L1Handler transaction, diverging from the proposer's authoritative record.

---

### Impact Explanation

Every validator node stores every L1Handler transaction with `paid_fee_on_l1 = Fee(1)` instead of the real L1 fee. This produces:

1. **Wrong block state on all non-proposer nodes** — the stored `paid_fee_on_l1` field in the block is fabricated, diverging from the proposer's authoritative value.
2. **Fee check bypass** — the blockifier's only fee adequacy guard (`paid_fee == Fee(0)`) is trivially satisfied by the hardcoded `Fee(1)`, meaning a validator would accept an L1Handler transaction even if the real `paid_fee_on_l1` were 0 (a condition the proposer would reject).
3. **Wrong authoritative RPC/trace view** — any RPC call or execution trace served from a validator node that surfaces `paid_fee_on_l1` returns `1` instead of the real value, matching the "authoritative-looking wrong value" impact class.

The `ProposalCommitment` and block hash are not affected because `paid_fee_on_l1` is not part of the transaction hash computation or any commitment field. Execution state changes and events are identical regardless of the fee value.

---

### Likelihood Explanation

This fires on **every** L1Handler transaction that passes through the consensus validator path — which is the normal operating path for all non-proposer nodes. No special attacker input is required; the bug is structural and unconditional. The TODO comment confirms the developers are aware the value is a placeholder.

---

### Recommendation

1. Carry `paid_fee_on_l1` through the consensus wire format. Add the field to the `L1HandlerV0` protobuf message and populate it in both the `From<L1HandlerTransaction>` serialiser and the `TryFrom<protobuf::L1HandlerV0>` deserialiser.
2. Remove the hardcoded `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler` and use the deserialized value instead.
3. Add a validation step in `is_proposal_init_valid` or `handle_proposal_part` that rejects L1Handler transactions whose `paid_fee_on_l1` is zero, mirroring the blockifier's intent.

---

### Proof of Concept

1. A proposer node receives an L1Handler transaction from the L1 scraper with `paid_fee_on_l1 = X` (e.g., `X = 1_000_000_000_000_000` wei).
2. The proposer serialises it as `ConsensusTransaction::L1Handler(tx.tx)` — `paid_fee_on_l1` is dropped because `L1HandlerV0` has no such field.
3. A validator node receives the message, calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler`, and constructs `L1HandlerTransaction { ..., paid_fee_on_l1: Fee(1) }`.
4. The blockifier executes the transaction; the check `if paid_fee == Fee(0)` passes because `Fee(1) != Fee(0)`.
5. The validator commits the block with `paid_fee_on_l1 = 1` in its storage.
6. An RPC call to `starknet_getTransactionReceipt` or a re-execution trace on the validator returns `paid_fee_on_l1 = 1` — an authoritative-looking wrong value — for every L1Handler transaction in every block. [1](#0-0) [4](#0-3) [3](#0-2)

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L197-201)
```rust
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L987-995)
```rust
impl From<L1HandlerTransaction> for protobuf::L1HandlerV0 {
    fn from(value: L1HandlerTransaction) -> Self {
        Self {
            nonce: Some(value.nonce.0.into()),
            address: Some(value.contract_address.into()),
            entry_point_selector: Some(value.entry_point_selector.0.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
        }
    }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L103-113)
```rust
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-394)
```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            hash_value: tx.tx_hash,
            contract_address: tx.tx.contract_address,
            entry_point_selector: tx.tx.entry_point_selector,
            calldata: tx.tx.calldata,
            nonce: tx.tx.nonce,
            paid_fee_on_l1: tx.paid_fee_on_l1,
        }
    }
}
```

**File:** echonet/os_input_builder.py (L277-290)
```python
def _l1_handler_to_executable(central_tx: JsonObject) -> JsonObject:
    """Central `L1_HANDLER` → executable; the inner `version` field is always 0."""
    inner = {
        "version": "0x0",
        "nonce": central_tx["nonce"],
        "contract_address": central_tx["contract_address"],
        "entry_point_selector": central_tx["entry_point_selector"],
        "calldata": central_tx["calldata"],
    }
    return {
        "tx": inner,
        "tx_hash": central_tx["hash_value"],
        "paid_fee_on_l1": central_tx["paid_fee_on_l1"],
    }
```
