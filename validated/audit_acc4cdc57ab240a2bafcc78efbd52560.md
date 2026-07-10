### Title
Zero `BlockConfirmations` Bypasses Bitcoin Finality Check, Enabling Double-Spend Attestation - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`, `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

---

### Summary

The `verify_foreign_transaction` contract method accepts a `BitcoinRpcRequest` with `confirmations: 0` without any on-chain or node-side minimum validation. Because the node-side confirmation check is `block_confirmations_threshold <= transaction_block_confirmation`, a threshold of `0` is always satisfied by any `u64` value, completely bypassing Bitcoin finality enforcement. An unprivileged caller can obtain a valid MPC threshold signature attesting to a Bitcoin transaction that has zero required confirmations, enabling double-spend attacks in bridge flows.

---

### Finding Description

`BitcoinRpcRequest` carries a `confirmations: BlockConfirmations` field that the caller supplies freely:

```rust
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,   // caller-controlled, no minimum
    pub extractors: Vec<BitcoinExtractor>,
}
```

`BlockConfirmations` is a plain `u64` newtype with no lower-bound constraint:

```rust
pub struct BlockConfirmations(u64);
```

The ABI schema explicitly permits `minimum: 0.0`. The contract's `verify_foreign_transaction` entry point performs no validation of this field — it only checks that the chain is whitelisted and enqueues the yield request.

On the node side, `BitcoinInspector::extract` enforces:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;

if !enough_block_confirmations {
    return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { ... });
}
```

When `block_confirmations_threshold = 0`, the inequality `0 <= actual_confirmations` is trivially true for every `u64`, so the guard never fires. The node proceeds to sign the payload regardless of how many confirmations the Bitcoin transaction actually has.

Crucially, `NotEnoughBlockConfirmations` is classified as a **transient** error (nodes that hit it simply abstain from signing). With a threshold of `0`, no node ever abstains — all nodes participate and the threshold signature is produced.

---

### Impact Explanation

In the primary use case (Omnibridge inbound: Bitcoin → NEAR), the MPC signature is the on-chain proof that a Bitcoin transaction finalized. With `confirmations: 0`:

1. Attacker broadcasts a Bitcoin transaction (e.g., depositing to a bridge address).
2. Before any block confirmation, attacker calls `verify_foreign_transaction` with `confirmations: 0`.
3. All MPC nodes pass the confirmation check and co-sign the payload.
4. Attacker redeems the MPC signature on NEAR to claim bridged funds.
5. Attacker double-spends the original Bitcoin transaction (it was never confirmed).

This is a forged foreign-chain verification that causes invalid bridge execution and double-spend conditions — matching the **High** impact tier.

---

### Likelihood Explanation

- Any unprivileged NEAR account can call `verify_foreign_transaction` with a deposit of 1 yoctoNEAR.
- No special role, key, or collusion is required.
- The `confirmations` field is a plain integer in the JSON request; setting it to `0` requires no exploit tooling.
- The Bitcoin transaction only needs to be visible to the RPC nodes (mempool is sufficient for `getrawtransaction` on most Bitcoin RPC providers).

---

### Recommendation

Enforce a minimum confirmation count at the contract level before enqueuing the request. Reject any `BitcoinRpcRequest` with `confirmations == 0` (or below a protocol-defined floor, e.g., `1`):

```rust
// In verify_foreign_transaction, before enqueue_yield_request:
if let ForeignChainRpcRequest::Bitcoin(ref btc) = request.request {
    if btc.confirmations == BlockConfirmations::from(0) {
        env::panic_str("Bitcoin confirmations must be at least 1");
    }
}
```

Alternatively, enforce the minimum inside `BlockConfirmations` construction or add a `validate()` method on `BitcoinRpcRequest` called from the contract. A protocol-wide minimum (e.g., 6 for Bitcoin) should be documented and enforced on-chain so callers cannot weaken finality guarantees.

---

### Proof of Concept

1. Deploy the contract and register Bitcoin as a supported foreign chain.
2. Broadcast a Bitcoin transaction `T` to the mempool (0 confirmations).
3. Call:
   ```json
   verify_foreign_transaction({
     "request": {
       "Bitcoin": {
         "tx_id": "<T's txid>",
         "confirmations": 0,
         "extractors": ["BlockHash"]
       }
     },
     "domain_id": <foreign_tx_domain>,
     "payload_version": 1
   })
   ```
4. Each MPC node runs `BitcoinInspector::extract(tx_id, BlockConfirmations(0), ...)`. The check `0 <= rpc_response.confirmations` passes unconditionally.
5. All nodes co-sign; the contract resolves the yield with a valid `VerifyForeignTransactionResponse`.
6. Use the returned signature on NEAR to claim bridged funds, then double-spend `T` on Bitcoin.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L267-271)
```rust
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,
    pub extractors: Vec<BitcoinExtractor>,
}
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L183-184)
```rust
#[derive(From, Debug, Display, Clone, Copy, Deref, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct BlockConfirmations(u64);
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L265-274)
```rust
impl ForeignChainInspectionError {
    pub fn is_transient(&self) -> bool {
        matches!(
            self,
            Self::ClientError(_)
                | Self::RpcRequestFailed(_)
                | Self::NotFinalized
                | Self::NotEnoughBlockConfirmations { .. }
        )
    }
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L50-59)
```rust
        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
        }
```

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        let requested_chain = request.request.chain();
        let supported_chains = self.get_supported_foreign_chains();
        if !supported_chains.contains(&requested_chain) {
            env::panic_str(
                &InvalidParameters::ForeignChainNotSupported {
                    requested: requested_chain,
                }
                .to_string(),
            );
        }

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
    }
```
