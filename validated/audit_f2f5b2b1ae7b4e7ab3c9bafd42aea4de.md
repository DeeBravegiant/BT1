### Title
Missing Minimum Confirmation Validation Allows Signing of Insufficiently-Confirmed Bitcoin Transactions - (`crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

### Summary

The `BitcoinInspector::extract` function accepts a user-supplied `BlockConfirmations(0)` threshold, causing the confirmation check `block_confirmations_threshold <= transaction_block_confirmation` to trivially pass for any confirmed transaction. An unprivileged caller can obtain a valid MPC threshold signature attesting to a Bitcoin transaction with as few as 1 confirmation, bypassing the intended finality requirement and enabling double-spend attacks on bridges that rely on the MPC network's foreign-chain verification.

### Finding Description

The `verify_foreign_transaction` contract method accepts a `BitcoinRpcRequest` with a user-controlled `confirmations: BlockConfirmations` field. The contract performs no minimum validation on this value. [1](#0-0) 

When MPC nodes process the request, `BitcoinInspector::extract` performs the confirmation check:

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
``` [2](#0-1) 

With `block_confirmations_threshold == BlockConfirmations(0)`, the condition `0 <= transaction_block_confirmation` is always `true` for any confirmed transaction, since `BlockConfirmations` wraps a `u64`. [3](#0-2) 

The `BlockConfirmations` type has no enforced minimum: [3](#0-2) 

The signed payload (`ForeignTxSignPayloadV1`) includes the original request (with `confirmations: 0`), so the MPC network produces a valid threshold signature attesting to a transaction verified with zero confirmation requirement. [4](#0-3) 

**Analogy to M-04:** In M-04, `startedAt == 0` causes `block.timestamp - 0 > GRACE_PERIOD_TIME` to always be true, bypassing the sequencer uptime check. Here, `confirmations == 0` causes `0 <= transaction_block_confirmation` to always be true, bypassing the Bitcoin finality check. Both are cases where a zero value in a status/validity check causes the check to be silently bypassed.

### Impact Explanation

A bridge contract relying on the MPC network to enforce Bitcoin finality before releasing funds on NEAR can be exploited:

1. Attacker submits a Bitcoin transaction T to the bridge address.
2. Waits for 1 block confirmation (minimum for `blockhash` to be present in the RPC response, required for `verify_block_is_canonical` to succeed).
3. Calls `verify_foreign_transaction` with `confirmations: BlockConfirmations(0)`.
4. MPC nodes evaluate `0 <= 1` → `true`; the confirmation check is bypassed; nodes sign the payload.
5. Attacker uses the MPC signature to claim bridge funds on NEAR.
6. Attacker executes a Bitcoin double-spend (a 1-confirmation reorg is significantly easier than a 6-confirmation reorg).
7. Bridge funds are lost; the attested Bitcoin transaction no longer exists on the canonical chain.

This constitutes **forged foreign-chain verification enabling double-spend conditions** — a High impact per the allowed scope.

### Likelihood Explanation

The attack requires only:
- A NEAR account with the minimum deposit (1 yoctoNEAR).
- A Bitcoin transaction with at least 1 confirmation (so `blockhash` is present in the RPC response).
- Knowledge to set `confirmations: BlockConfirmations(0)` in the request.

No privileged access, threshold collusion, or cryptographic break is required. `verify_foreign_transaction` is a public, unprivileged contract method callable by any NEAR account. [5](#0-4) 

### Recommendation

Add a minimum confirmation requirement at the contract level. In `verify_foreign_transaction`, validate that `BitcoinRpcRequest.confirmations >= MINIMUM_BITCOIN_CONFIRMATIONS` (e.g., 1 as an absolute floor, with a recommended production value of 6) before enqueuing the request. Alternatively, enforce a protocol-level minimum inside `BitcoinInspector::extract` regardless of the user-supplied threshold, so the node-side check cannot be bypassed even if the contract-level check is absent.

### Proof of Concept

1. Deploy a bridge contract that calls `verify_foreign_transaction` and releases NEAR funds upon receiving a valid MPC signature.
2. Submit Bitcoin transaction T to the bridge address.
3. Wait for 1 Bitcoin block confirmation.
4. Call `verify_foreign_transaction` on the MPC contract:
   ```
   BitcoinRpcRequest {
       tx_id: T,
       confirmations: BlockConfirmations(0),   // ← bypasses the check
       extractors: [BitcoinExtractor::BlockHash],
   }
   ```
5. MPC nodes execute `0 <= 1` → `true`; `verify_block_is_canonical` succeeds; threshold signature is issued.
6. Submit the MPC signature to the bridge contract to claim NEAR funds.
7. Execute a Bitcoin double-spend on T (1-confirmation reorg is feasible for a well-resourced attacker or miner).
8. Bridge NEAR funds are drained; Bitcoin transaction T is gone from the canonical chain. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L517-542)
```rust
    #[handle_result]
    #[payable]
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
```

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L33-70)
```rust
    async fn extract(
        &self,
        transaction: BitcoinTransactionHash,
        block_confirmations_threshold: BlockConfirmations,
        extractors: Vec<BitcoinExtractor>,
    ) -> Result<Vec<BitcoinExtractedValue>, ForeignChainInspectionError> {
        let request_parameters = GetRawTransactionArgs {
            transaction_hash: TransportBitcoinTransactionHash::from(*transaction),
            verbose: VERBOSE_RESPONSE,
        };

        // TODO(#1978): add retry mechanism if the error from the request is transient
        let rpc_response: GetRawTransactionVerboseResponse = self
            .client
            .request(GET_RAW_TRANSACTION_METHOD, &request_parameters)
            .await?;

        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
        }

        self.verify_block_is_canonical(rpc_response.blockhash)
            .await?;

        let extracted_values = extractors
            .iter()
            .map(|extractor| extractor.extract_value(&rpc_response))
            .collect();

        Ok(extracted_values)
    }
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L183-184)
```rust
#[derive(From, Debug, Display, Clone, Copy, Deref, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct BlockConfirmations(u64);
```
