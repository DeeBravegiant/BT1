### Title
Attacker-Controlled Confirmation Threshold Bypasses Bitcoin Finality in `verify_foreign_transaction` — (File: `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

---

### Summary

An unprivileged NEAR contract caller submits a `verify_foreign_transaction` request with `BitcoinRpcRequest { confirmations: BlockConfirmations(0) }`. The contract performs no lower-bound validation on this field. MPC nodes consume the caller-supplied value verbatim as the confirmation threshold, so the finality check trivially passes for any transaction. The nodes then produce a threshold signature over the unconfirmed transaction payload, enabling a double-spend attack against any Bitcoin bridge that relies on this flow.

---

### Finding Description

**Entry point.** `verify_foreign_transaction` in `crates/contract/src/lib.rs` is a `#[payable]` public method callable by any NEAR account. It accepts a `VerifyForeignTransactionRequestArgs` containing a `ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest { tx_id, confirmations, extractors })`. [1](#0-0) 

**Contract-side validation.** The contract checks the domain, gas attachment, deposit, and whether the chain is in the supported-chains set. It performs **no validation of the `confirmations` value**. [2](#0-1) 

**Node-side processing.** `execute_foreign_chain_request` in `crates/node/src/providers/verify_foreign_tx/sign.rs` passes the caller-supplied `request.confirmations` directly to `BitcoinInspector::extract` as `block_confirmations_threshold`. [3](#0-2) 

**Finality check.** Inside `BitcoinInspector::extract`, the only finality gate is:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;
if !enough_block_confirmations {
    return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { … });
}
``` [4](#0-3) 

When the attacker supplies `BlockConfirmations(0)`, the predicate `0 <= transaction_block_confirmation` is unconditionally true for any transaction the RPC returns (confirmations are non-negative). The check is bypassed entirely, and execution continues to `verify_block_is_canonical` and the extractor loop, ultimately producing a valid `ForeignTxSignPayload` that the MPC network signs.

**Type definition.** `BlockConfirmations` is a plain newtype with no minimum-value invariant enforced at construction time. [5](#0-4) 

**Same class on EVM chains.** The `EvmFinality::Latest` variant is accepted by the contract and passed verbatim to `EvmInspector::verify_finality_level`. With `Latest`, the head-block check `head.number >= receipt_block_number` is always satisfied for any included transaction, bypassing `Safe`/`Finalized` requirements on Abstract, BNB, Base, Arbitrum, HyperEVM, and Polygon. [6](#0-5) 

---

### Impact Explanation

**Impact: High — forged foreign-chain verification enabling double-spend.**

A Bitcoin bridge built on this flow relies on the MPC network refusing to sign until the deposit transaction has reached a safe confirmation depth (typically 6). By supplying `confirmations: 0`, the attacker receives a valid MPC signature over a zero-confirmation transaction. They can immediately redeem the corresponding NEAR-side asset, then broadcast a conflicting Bitcoin transaction (RBF or CPFP) to reverse the deposit. The bridge loses the bridged value; the attacker profits the full bridged amount.

The same logic applies to EVM chains with `EvmFinality::Latest`: a transaction in the latest block can be reorganized, and the attacker can claim NEAR-side assets before the reorg is detected.

---

### Likelihood Explanation

**Likelihood: High.**

- No privileged role, key material, or collusion is required.
- Any NEAR account that can pay the minimum deposit (`MINIMUM_SIGN_REQUEST_DEPOSIT`) and attach the required gas can call `verify_foreign_transaction`.
- The attack is deterministic: setting `confirmations: 0` always bypasses the check.
- Bitcoin RBF (Replace-By-Fee) is widely available; a zero-confirmation double-spend is a well-understood, low-cost operation.

---

### Recommendation

**Short term.** Enforce a minimum confirmation threshold in the contract's `verify_foreign_transaction` handler. Reject any `BitcoinRpcRequest` with `confirmations` below a protocol-defined floor (e.g., 6 for Bitcoin mainnet). Similarly, reject `EvmFinality::Latest` for chains where reorg risk is material; require at least `Safe` or `Finalized`.

**Long term.** Move the minimum-confirmation policy on-chain alongside the `ForeignChainRpcWhitelist` so that the threshold is voted in by node operators and cannot be undercut by a single caller. The `ChainEntry` structure already holds per-chain configuration; a `min_confirmations` field fits naturally there.

---

### Proof of Concept

```rust
// Any NEAR account calls verify_foreign_transaction with confirmations: 0
contract.verify_foreign_transaction(VerifyForeignTransactionRequestArgs {
    domain_id: DomainId::default().0.into(),
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: BitcoinTxId(<attacker_unconfirmed_tx_hash>),
        confirmations: BlockConfirmations(0),   // ← attacker-controlled, no floor enforced
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
});
// MPC nodes call BitcoinInspector::extract with block_confirmations_threshold = 0
// Predicate: 0 <= rpc_response.confirmations  →  always true
// Nodes proceed to sign; attacker receives threshold signature over unconfirmed tx
// Attacker redeems NEAR-side asset, then RBF-double-spends the Bitcoin deposit
``` [7](#0-6) [5](#0-4) [2](#0-1)

### Citations

**File:** crates/contract/src/lib.rs (L517-557)
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L131-150)
```rust
            dtos::ForeignChainRpcRequest::Bitcoin(request) => {
                let inspector = self
                    .inspectors
                    .bitcoin
                    .as_ref()
                    .context("no inspector configured for bitcoin")?;
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
                    .timeout(FOREIGN_CHAIN_INSPECTION_TIMEOUT)
                    .await
                    .context("timed out during execution of foreign chain request")??;
                extracted_values.into_iter().map(Into::into).collect()
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L38-59)
```rust
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
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L267-271)
```rust
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,
    pub extractors: Vec<BitcoinExtractor>,
}
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L100-125)
```rust
    /// Checks that the receipt's block has reached the requested finality level — i.e. that the
    /// head of the chain at `finality` is at or past `receipt_block_number`.
    async fn verify_finality_level(
        &self,
        receipt_block_number: U64,
        finality: EthereumFinality,
    ) -> Result<(), ForeignChainInspectionError> {
        let finality_tag = match finality {
            EthereumFinality::Finalized => FinalityTag::Finalized,
            EthereumFinality::Safe => FinalityTag::Safe,
            EthereumFinality::Latest => FinalityTag::Latest,
        };
        let args = GetBlockByNumberArgs::new(
            BlockNumberOrTag::Tag(finality_tag),
            ReturnFullTransactionObjects::from(false),
        );
        let head: GetBlockByNumberResponse = self
            .client
            .request(GET_BLOCK_BY_NUMBER_METHOD, &args)
            .await?;

        if head.number < receipt_block_number {
            return Err(ForeignChainInspectionError::NotFinalized);
        }
        Ok(())
    }
```
