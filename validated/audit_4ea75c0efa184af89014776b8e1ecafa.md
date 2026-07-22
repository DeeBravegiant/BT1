### Title
Hardcoded `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler` Binds Wrong `paid_fee_on_l1` for Every Validator-Executed L1 Handler Transaction — (File: `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

When the validator converts a consensus-received L1 handler transaction into an executable one, it unconditionally supplies `Fee(1)` as `paid_fee_on_l1` instead of the real fee paid on L1. The `ConsensusTransaction::L1Handler` wire type carries only `transaction::L1HandlerTransaction` (which has no `paid_fee_on_l1` field), so the real value is silently dropped by the proposer before broadcast and can never be recovered by the validator. The hardcoded `Fee(1)` is the wrong value for every L1 handler whose actual `paid_fee_on_l1 ≠ 1`.

---

### Finding Description

`ConsensusTransaction` is defined as:

```rust
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),   // ← no paid_fee_on_l1
}
``` [1](#0-0) 

`transaction::L1HandlerTransaction` (the raw starknet-api type) has no `paid_fee_on_l1` field. The field lives only on `executable_transaction::L1HandlerTransaction`:

```rust
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,          // ← only here
}
``` [2](#0-1) 

When the proposer serialises an `InternalConsensusTransaction::L1Handler` for broadcast it strips `paid_fee_on_l1`. When the validator deserialises the message and calls `convert_consensus_l1_handler_to_internal_l1_handler`, it reconstructs the executable transaction with a hardcoded placeholder:

```rust
fn convert_consensus_l1_handler_to_internal_l1_handler(
    &self,
    tx: transaction::L1HandlerTransaction,
) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
    Ok(executable_transaction::L1HandlerTransaction::create(
        tx,
        &self.chain_id,
        // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
        Fee(1),                        // ← always wrong
    )?)
}
``` [3](#0-2) 

The blockifier's L1-handler execution path reads `self.paid_fee_on_l1` and enforces:

```rust
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(Box::new(
        TransactionFeeError::InsufficientFee { paid_fee, actual_fee: receipt.fee },
    )));
}
``` [4](#0-3) 

Because the current guard is only `== Fee(0)`, `Fee(1)` always passes, masking the wrong value today. However, the `paid_fee_on_l1` field is also serialised into the `CentralL1HandlerTransaction` blob that is forwarded to the centralised recorder:

```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            ...
            paid_fee_on_l1: tx.paid_fee_on_l1,   // ← Fee(1) on validator path
        }
    }
}
``` [5](#0-4) 

---

### Impact Explanation

**Immediate (current code):** The fee guard checks only `!= 0`, so `Fee(1)` passes for every L1 handler the proposer legitimately includes. No execution-result divergence occurs today.

**Forward-looking / latent:** The TODO comment explicitly acknowledges that the guard will be tightened to compare `paid_fee_on_l1` against the actual execution cost. Once that happens, the validator will reject every L1 handler whose real `paid_fee_on_l1 < actual_fee` (i.e., almost all of them, since `Fee(1)` is 1 wei/fri). The proposer would accept those transactions; the validator would reject them. This is a consensus-breaking divergence: the validator would refuse to sign a valid proposal, stalling block production.

Additionally, any downstream consumer of the cende blob that relies on `paid_fee_on_l1` (e.g., L1 message accounting, fee-market analytics) receives `Fee(1)` instead of the real value for every validator-originated blob.

Matches: **High — Transaction conversion binds the wrong executable payload (`paid_fee_on_l1`).**

---

### Likelihood Explanation

Every L1 handler transaction that travels through the consensus path (proposer → validator) triggers this code path unconditionally. The trigger requires no special privilege; it fires for every normal L1→L2 message included in a block. The bug is latent today but will activate the moment the fee-sufficiency check is strengthened, which the codebase explicitly plans to do.

---

### Recommendation

1. Add `paid_fee_on_l1: Fee` to `ConsensusTransaction::L1Handler` (or to a wrapper) so the real value is transmitted over the wire.
2. In `convert_consensus_l1_handler_to_internal_l1_handler`, read `tx.paid_fee_on_l1` from the incoming consensus message instead of hardcoding `Fee(1)`.
3. Remove the TODO comment and the placeholder once the wire type carries the field.

---

### Proof of Concept

1. L1 provider delivers an L1 handler with `paid_fee_on_l1 = Fee(500_000_000_000)` to the proposer.
2. Proposer executes it successfully (real fee passes the `!= 0` guard), includes it in the block proposal, and broadcasts `ConsensusTransaction::L1Handler(raw_tx)` — `paid_fee_on_l1` is absent from the wire message.
3. Validator receives the message, calls `convert_consensus_l1_handler_to_internal_l1_handler`, and constructs `executable_transaction::L1HandlerTransaction { paid_fee_on_l1: Fee(1), … }`.
4. **Today:** validator executes, `Fee(1) != Fee(0)` passes, no divergence.
5. **After fee-check tightening** (e.g., `paid_fee < actual_fee` → reject): validator computes `actual_fee ≈ 500_000_000_000`, sees `Fee(1) < actual_fee`, returns `InsufficientFee`, and rejects the transaction — diverging from the proposer's accepted result and refusing to sign the proposal. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/consensus_transaction.rs (L8-18)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),
}
```

**File:** crates/starknet_api/src/executable_transaction.rs (L380-385)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,
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

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L98-115)
```rust
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
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

                        Ok(l1_handler_tx_execution_info(execute_call_info, receipt, None))
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-393)
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
```
