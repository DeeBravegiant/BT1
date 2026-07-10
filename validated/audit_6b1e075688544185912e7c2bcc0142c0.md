### Title
User-Controlled `BlockConfirmations(0)` Bypasses Bitcoin Finality Check in `verify_foreign_transaction` - (File: crates/foreign-chain-inspector/src/bitcoin/inspector.rs)

### Summary

The `verify_foreign_transaction` endpoint accepts a user-supplied `BlockConfirmations` value with no minimum validation at either the contract or node layer. Setting `BlockConfirmations(0)` causes the Bitcoin finality guard to pass for any transaction regardless of actual confirmation depth, allowing an unprivileged caller to obtain a valid MPC signature attesting to an insufficiently-confirmed (or unconfirmed) Bitcoin transaction.

### Finding Description

`BitcoinRpcRequest` carries a `confirmations: BlockConfirmations` field that is entirely user-controlled. The contract stores it verbatim and forwards it to MPC nodes without any lower-bound check. [1](#0-0) 

In the node's signing provider, the value is passed directly as the threshold to the inspector: [2](#0-1) 

The Bitcoin inspector then evaluates: [3](#0-2) 

When `block_confirmations_threshold` is `0`, the expression `0 <= transaction_block_confirmation` is always `true` for any confirmation count the RPC returns, including `0` (mempool transaction). The finality guard is completely bypassed.

The contract's `verify_foreign_transaction` performs no validation of the `confirmations` field before enqueuing the request: [4](#0-3) 

No minimum is enforced at any layer between user input and the inspector call.

### Impact Explanation

The MPC network's stated purpose for `verify_foreign_transaction` is to attest that a foreign-chain transaction has **finalized** before signing, enabling trustless bridge inbound flows (e.g., Omnibridge). Bridge contracts on NEAR are expected to trust that the MPC signature implies the required finality was verified.

With `BlockConfirmations(0)`, an attacker can:
1. Broadcast a Bitcoin transaction sending funds to a bridge deposit address.
2. Immediately submit `verify_foreign_transaction` with `confirmations: 0` before any block confirmation.
3. Receive a valid MPC threshold signature over the payload `(request, extracted_values)`.
4. Present the signature to the NEAR bridge contract to claim NEAR-side tokens.
5. Attempt to double-spend the Bitcoin transaction (replace-by-fee or, for lower-security chains, a shallow reorg).

The bridge has disbursed NEAR tokens for a Bitcoin transaction that never reached finality. This constitutes **forged foreign-chain verification enabling double-spend / invalid bridge execution**, matching the High allowed impact.

### Likelihood Explanation

The attack path is fully reachable by any unprivileged NEAR account with 1 yoctoNEAR deposit. No privileged key, threshold collusion, or TEE access is required. The attacker only needs to craft a `VerifyForeignTransactionRequestArgs` with `confirmations: BlockConfirmations(0)`. The vulnerability is present in the production code path for every Bitcoin `verify_foreign_transaction` call.

### Recommendation

Enforce a protocol-level minimum `BlockConfirmations` in the contract before enqueuing the request. The minimum should be a governance-controlled parameter (e.g., stored per-chain in `ForeignChainsMetadata` or in `Config`) and validated in `verify_foreign_transaction` before the request is stored:

```rust
// In verify_foreign_transaction, after chain support check:
if let ForeignChainRpcRequest::Bitcoin(ref btc_req) = request.request {
    let min_confirmations = self.get_min_bitcoin_confirmations(); // governance param
    if btc_req.confirmations.0 < min_confirmations {
        env::panic_str("BlockConfirmations below protocol minimum");
    }
}
```

Additionally, MPC nodes should independently enforce a floor on `BlockConfirmations` from their local configuration, so that even a malformed contract cannot instruct nodes to sign for unconfirmed transactions.

### Proof of Concept

1. Deploy the contract in its current state with a Bitcoin `ForeignTx` domain.
2. Call `verify_foreign_transaction` with:
   ```json
   {
     "request": {
       "Bitcoin": {
         "tx_id": "<32-byte hex of a mempool tx>",
         "confirmations": 0,
         "extractors": ["BlockHash"]
       }
     },
     "domain_id": 0,
     "payload_version": "V1"
   }
   ```
3. Observe that the contract enqueues the request without error.
4. MPC nodes call `getrawtransaction` on the mempool transaction (confirmations = 0); the check `0 <= 0` passes.
5. Nodes produce and submit a valid threshold signature via `respond_verify_foreign_tx`.
6. The caller receives a signed attestation for an unconfirmed Bitcoin transaction.

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1282-1282)
```rust
pub struct BlockConfirmations(pub u64);
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L137-146)
```rust
                let transaction_id = request.tx_id.0.into();
                let block_confirmations = request.confirmations.0.into();
                let extractors: Vec<BitcoinExtractor> = request
                    .extractors
                    .iter()
                    .cloned()
                    .map(TryInto::try_into)
                    .collect::<Result<_, _>>()?;
                let extracted_values = inspector
                    .extract(transaction_id, block_confirmations, extractors)
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L50-58)
```rust
        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
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
