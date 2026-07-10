### Title
Missing Minimum Finality/Confirmation Enforcement in `verify_foreign_transaction` — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` contract entry point accepts fully user-controlled finality parameters — `confirmations: BlockConfirmations` for Bitcoin and `finality: EvmFinality` for EVM chains — without enforcing any minimum threshold. An unprivileged caller can set `confirmations: 0` (Bitcoin) or `finality: Latest` (EVM) and obtain a valid threshold MPC signature for a foreign-chain transaction that has not yet reached meaningful finality. This enables a cross-chain double-spend: the attacker claims bridge funds on NEAR while the underlying foreign-chain transaction is still reversible via reorg.

---

### Finding Description

`verify_foreign_transaction` is the on-chain entry point for the Omnibridge inbound flow. Its stated purpose is to attest that a foreign-chain transaction has *finalized successfully* before the MPC network signs a payload that a NEAR bridge contract can use to release funds.

The function performs three checks before enqueuing the request:

1. Domain purpose is `ForeignTx`
2. Sufficient gas is attached
3. The requested chain is in the on-chain supported-chain set [1](#0-0) 

It does **not** validate the finality parameters supplied by the caller. For Bitcoin, the `confirmations` field of `BitcoinRpcRequest` is a plain `u64` wrapper with no lower bound: [2](#0-1) 

For EVM chains, the `finality` field accepts `EvmFinality::Latest`, the weakest option: [3](#0-2) 

The user-supplied values are forwarded verbatim to the MPC nodes. The Bitcoin inspector enforces only that the actual on-chain confirmation count meets the *caller-specified* threshold: [4](#0-3) 

When `block_confirmations_threshold = 1`, the check passes for any transaction with a single confirmation. When `finality = Latest`, the EVM inspector only requires the transaction to appear in the current chain tip: [5](#0-4) 

The signed payload commits to the full `ForeignChainRpcRequest`, including the caller-chosen `confirmations`/`finality` value: [6](#0-5) 

So the MPC network produces a cryptographically valid signature over a payload that encodes a weak finality requirement chosen by the attacker. Any bridge contract that does not independently re-validate the `confirmations` or `finality` field embedded in the signed payload will treat this signature as proof of finalization.

The on-chain `ChainEntry` configuration (voted in by participants) stores an RPC quorum but no minimum confirmation/finality floor: [7](#0-6) 

---

### Impact Explanation

**High — cross-chain double-spend / forged foreign-chain verification.**

Attack scenario (Bitcoin bridge):

1. Attacker deposits Bitcoin; the transaction receives 1 confirmation.
2. Attacker calls `verify_foreign_transaction` with `confirmations: 1` while the bridge protocol requires 6 for safety.
3. MPC nodes observe 1 confirmation ≥ threshold 1, pass the check, and collectively sign the payload.
4. Attacker submits the MPC signature to the NEAR bridge contract and receives NEAR-side funds.
5. A Bitcoin reorg reverses the 1-confirmation transaction.
6. Attacker retains the NEAR-side funds; the bridge has been drained without a valid deposit.

The same scenario applies to EVM chains with `finality: Latest` during a reorg window.

---

### Likelihood Explanation

Any unprivileged NEAR account can call `verify_foreign_transaction` with a 1 yoctoNEAR deposit. No special role or key is required. The attacker only needs to time the call to a moment when the foreign-chain transaction has the minimum number of confirmations they specify. For EVM chains with `Latest` finality, the window is every block. For Bitcoin with `confirmations: 1`, the window opens after the first confirmation. Reorgs of 1–2 blocks are routine on most EVM chains and occasionally occur on Bitcoin.

---

### Recommendation

Enforce minimum finality parameters at the contract level, either as hard-coded constants or as per-chain governance-configurable values stored in `ForeignChainsMetadata`/`ChainEntry`. For example:

- Bitcoin: require `confirmations >= MIN_BITCOIN_CONFIRMATIONS` (e.g., 6).
- EVM chains: reject `EvmFinality::Latest`; require at least `Safe` or `Finalized`.

These minimums should be checked inside `verify_foreign_transaction` before the request is enqueued, analogous to how the function already rejects unsupported chains: [8](#0-7) 

---

### Proof of Concept

```
1. Deploy a NEAR bridge contract that calls verify_foreign_transaction and
   releases NEAR tokens upon receiving a valid VerifyForeignTransactionResponse.

2. Send 1 BTC to the bridge deposit address on Bitcoin mainnet.

3. Wait for 1 Bitcoin confirmation (~10 minutes).

4. Call verify_foreign_transaction with:
     BitcoinRpcRequest {
         tx_id: <deposit_tx_id>,
         confirmations: BlockConfirmations(1),   // attacker-chosen minimum
         extractors: vec![BitcoinExtractor::BlockHash],
     }
   Attach 1 yoctoNEAR deposit.

5. MPC nodes query getrawtransaction, observe confirmations=1 >= threshold=1,
   pass verify_block_is_canonical, and collectively sign the payload.

6. The contract returns a VerifyForeignTransactionResponse with a valid
   threshold ECDSA signature over SHA-256(borsh(ForeignTxSignPayloadV1 {
       request: <above>, values: [BlockHash(<block>)] })).

7. Submit the response to the NEAR bridge contract; receive NEAR tokens.

8. Coordinate a 1-block Bitcoin reorg (or use a pre-arranged double-spend)
   to reverse the deposit transaction.

9. Attacker holds NEAR tokens; bridge has no corresponding Bitcoin deposit.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** crates/contract/src/lib.rs (L514-557)
```rust
    /// Submit a verification + signing request for a foreign chain transaction.
    /// MPC nodes will verify the transaction on the foreign chain before signing.
    /// The signed payload is derived from the transaction ID (hash of tx_id).
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

**File:** docs/foreign-chain-transactions.md (L135-139)
```markdown
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId, // This is the payload we're signing
    pub confirmations: BlockConfirmations, // required confirmations before considering final
    pub extractors: Vec<BitcoinExtractor>,
}
```

**File:** docs/foreign-chain-transactions.md (L141-145)
```markdown
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}
```

**File:** docs/foreign-chain-transactions.md (L176-189)
```markdown
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
```

The 32-byte `msg_hash` that nodes sign is computed as:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload))
```

Callers select the payload version via `VerifyForeignTransactionRequestArgs::payload_version`.
Borsh field ordering is stability-critical — fields and enum variants must never be reordered.
```

**File:** docs/foreign-chain-transactions.md (L299-310)
```markdown
  (`foreign_chain_rpc_whitelist`): there is a `ChainEntry` for it (trusted provider list + RPC
  quorum). The policy set every node is expected to cover; **no single operator can add or remove a
  chain — only a threshold vote can**. Returned by `get_whitelisted_foreign_chains()`. See
  [On-chain RPC Provider Whitelist](#on-chain-rpc-provider-whitelist).
- **RPC quorum** (`rpc_quorum(C)`) — per whitelisted chain `C`, how many of a node's configured
  providers must return the same response for that node to accept a verification result
  (`ChainEntry.quorum`), voted in alongside the provider list. A runtime knob; distinct from the
  *signing threshold*.
- **Signing threshold** — the cryptographic reconstruction threshold of the `ForeignTx` signing
  domain (`self.threshold()`): how many participants must produce signature shares to sign an
  observation. Distinct from the RPC quorum.
- **A node covers (supports) a chain `C`** — the node's local RPC config has at least `rpc_quorum(C)`
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L33-69)
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
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L100-124)
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
```
