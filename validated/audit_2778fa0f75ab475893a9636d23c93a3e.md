### Title
Zero `BlockConfirmations` Bypasses Bitcoin Finality Check in `verify_foreign_transaction` - (File: `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

### Summary
The `verify_foreign_transaction` contract endpoint accepts a user-supplied `confirmations` field inside `BitcoinRpcRequest` with no minimum-value enforcement. When a caller sets `confirmations: 0`, the MPC node's finality guard (`block_confirmations_threshold <= transaction_block_confirmation`) trivially passes for any transaction — including one with zero on-chain confirmations — causing the MPC network to issue a threshold signature over an unconfirmed Bitcoin transaction. A bridge contract that trusts this signature can be drained before the underlying Bitcoin transaction is ever mined.

### Finding Description

The `BitcoinRpcRequest` struct carries a caller-controlled `confirmations: BlockConfirmations` field that is forwarded verbatim from the user's call all the way into the node-side finality check.

**Contract side — no validation:**

`verify_foreign_transaction` validates domain existence, gas, deposit, and chain support, but never inspects the `confirmations` value. [1](#0-0) 

**Node side — trivially bypassable guard:**

Inside `BitcoinInspector::extract`, the only finality gate is:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;
if !enough_block_confirmations {
    return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { … });
}
``` [2](#0-1) 

`BlockConfirmations` is a plain `u64` newtype. When `block_confirmations_threshold = 0`, the comparison `0 <= transaction_block_confirmation` is unconditionally `true` for every `u64` value, including `0`. The guard is therefore dead code for any request that supplies `confirmations: 0`.

The signed payload (`ForeignTxSignPayloadV1`) embeds the full `ForeignChainRpcRequest`, so the resulting signature covers `confirmations: 0` — a request that explicitly asserts no finality requirement was applied. [3](#0-2) 

### Impact Explanation

An attacker obtains a valid threshold MPC signature over an unconfirmed (0-confirmation) Bitcoin transaction. Any bridge or NEAR contract that relies on `verify_foreign_transaction` to gate an inbound transfer will accept this signature and release funds before the Bitcoin transaction is finalized. The attacker can then double-spend the Bitcoin-side transaction (RBF, mempool eviction, or simply never broadcast it), resulting in direct theft of the bridge-side funds. This matches the allowed impact: **High — forged foreign-chain verification that causes invalid bridge execution or double-spend conditions.**

### Likelihood Explanation

The attack requires only a 1-yoctoNEAR deposit and a valid NEAR account — no privileged role, no threshold collusion, no key material. The `confirmations` field is a plain integer in the JSON/Borsh-encoded request; any caller can set it to `0`. The only prerequisite is that a `ForeignTx`-purpose domain and a Bitcoin chain policy are configured on the contract, both of which are expected in production.

### Recommendation

1. **Contract-level guard**: Reject `BitcoinRpcRequest` with `confirmations == 0` inside `verify_foreign_transaction` (or in a shared validation helper), returning a typed `InvalidParameters` error.
2. **Node-level guard**: Add an explicit `if block_confirmations_threshold == 0 { return Err(…) }` check at the top of `BitcoinInspector::extract` as defense-in-depth.
3. **Type-level enforcement**: Consider making `BlockConfirmations` a non-zero newtype (e.g., wrapping `std::num::NonZeroU64`) so the zero case is rejected at deserialization time.

### Proof of Concept

1. Deploy the contract with a `ForeignTx` domain and a Bitcoin chain policy (as in the localnet setup).
2. Broadcast a Bitcoin transaction but do **not** wait for confirmation.
3. Call `verify_foreign_transaction` with:
   ```json
   {
     "request": {
       "Bitcoin": {
         "tx_id": "<unconfirmed_tx_id>",
         "confirmations": 0,
         "extractors": ["BlockHash"]
       }
     },
     "domain_id": <foreign_tx_domain_id>,
     "payload_version": 1
   }
   ```
   with a 1-yoctoNEAR deposit.
4. MPC nodes query `getrawtransaction`; the response has `confirmations: 0`. The check `0 <= 0` passes.
5. Nodes sign and return a `VerifyForeignTransactionResponse` containing a valid threshold ECDSA signature.
6. Present this signature to the bridge contract to claim inbound funds.
7. Double-spend or drop the Bitcoin transaction — the bridge has already released funds.

### Citations

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

**File:** crates/contract/src/lib.rs (L3687-3693)
```rust
        let payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: request.request.clone(),
            values: vec![ExtractedValue::BitcoinExtractedValue(
                BitcoinExtractedValue::BlockHash([42u8; 32].into()),
            )],
        });
        let payload_hash = payload.compute_msg_hash().unwrap().0;
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
