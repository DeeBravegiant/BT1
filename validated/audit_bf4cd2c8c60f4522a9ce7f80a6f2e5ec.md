### Title
Caller-Controlled Finality Parameters in Unprotected `verify_foreign_transaction` Enable Forged Bridge Attestations via Reorg - (File: `crates/contract/src/lib.rs`, `crates/node/src/providers/verify_foreign_tx/sign.rs`)

---

### Summary

`verify_foreign_transaction` is a public function callable by any NEAR account. The caller fully controls the `finality` field (for EVM chains: `Latest`, `Safe`, `Finalized`) and the `confirmations` field (for Bitcoin: any `u64` including `0` or `1`). The contract performs no minimum-finality enforcement. MPC nodes pass these caller-supplied parameters directly to the chain inspector, which uses them as the sole finality gate. An attacker can obtain a valid MPC threshold signature attesting to a foreign-chain transaction that has not reached economic finality, enabling a double-spend or invalid bridge execution.

---

### Finding Description

`verify_foreign_transaction` is declared `#[payable]` with no access control beyond a 1-yoctoNEAR deposit:

```rust
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    self.check_request_preconditions(
        request.domain_id,
        DomainPurpose::ForeignTx,
        Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
        MINIMUM_SIGN_REQUEST_DEPOSIT,   // 1 yoctoNEAR
    );
    // ... no finality validation ...
    let request = args_into_verify_foreign_tx_request(request);
    // enqueued as-is
``` [1](#0-0) 

The `VerifyForeignTransactionRequestArgs` struct carries a caller-supplied `finality` (for EVM chains) or `confirmations` (for Bitcoin) field that is stored verbatim in the pending request and later forwarded to the node-side inspector:

```rust
async fn execute_foreign_chain_request(
    &self,
    request: &dtos::ForeignChainRpcRequest,
    payload_version: dtos::ForeignTxPayloadVersion,
) -> anyhow::Result<dtos::ForeignTxSignPayload> {
    // ...
    dtos::ForeignChainRpcRequest::Bitcoin(request) => {
        let block_confirmations = request.confirmations.0.into(); // caller-supplied
        inspector.extract(transaction_id, block_confirmations, extractors)...
    }
    dtos::ForeignChainRpcRequest::Abstract(request) => {
        let finality: EthereumFinality = request.finality.clone().try_into()?; // caller-supplied
        inspector.extract(transaction_id, finality, extractors)...
    }
``` [2](#0-1) 

The Bitcoin inspector enforces only `block_confirmations_threshold <= transaction_block_confirmation`. If the caller passes `confirmations: 0`, the check `0 <= N` is always true for any confirmation count, including unconfirmed mempool transactions:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;
if !enough_block_confirmations {
    return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { ... });
}
``` [3](#0-2) 

For EVM chains, the inspector checks only that the chain head at the requested finality tag is at or past the receipt block number. With `finality: Latest`, this is the tip of the chain — a block that can be reorged:

```rust
let finality_tag = match finality {
    EthereumFinality::Finalized => FinalityTag::Finalized,
    EthereumFinality::Safe => FinalityTag::Safe,
    EthereumFinality::Latest => FinalityTag::Latest,   // reorg-able
};
``` [4](#0-3) 

The signed payload (`ForeignTxSignPayload::V1`) commits to the full `request` including the caller-supplied `finality`/`confirmations`, but the MPC network has already produced a valid threshold signature over it. A downstream NEAR bridge contract that does not re-inspect the finality field in the signed payload will accept this signature as a trusted attestation. [5](#0-4) 

---

### Impact Explanation

The primary use case documented for `verify_foreign_transaction` is the **Omnibridge inbound flow**: a user deposits funds on a foreign chain, submits a `verify_foreign_transaction` request, and the MPC signature is used to unlock equivalent assets on NEAR. If the MPC signs for a `Latest`-finality EVM transaction or a `confirmations: 0` Bitcoin transaction, an attacker can:

1. Deposit funds on Ethereum/Bitcoin.
2. Immediately call `verify_foreign_transaction` with `finality: Latest` / `confirmations: 0`.
3. Receive a valid MPC threshold signature attesting to the deposit.
4. Submit the signature to the NEAR bridge contract to claim NEAR-side tokens.
5. Cause or wait for the foreign-chain transaction to be reorged (or double-spend the UTXO for Bitcoin).
6. Recover the original foreign-chain funds.

The attacker ends up with both the NEAR-side tokens and the original foreign-chain funds. This matches the **High** impact category: "forged foreign-chain verification... that causes invalid bridge execution or double-spend conditions."

---

### Likelihood Explanation

- The function is callable by any NEAR account with 1 yoctoNEAR.
- `EvmFinality::Latest` and `BlockConfirmations(1)` (or `0`) are valid enum/integer values accepted by the contract without any floor check.
- EVM chains (Ethereum, Abstract, BNB, Base, Arbitrum, HyperEVM, Polygon) all accept `Latest` finality.
- Bitcoin reorgs at 1 confirmation are rare but historically documented; 0-confirmation double-spends are a known Bitcoin attack vector.
- No economic deterrent exists: the 1-yoctoNEAR deposit is negligible relative to bridge amounts.

---

### Recommendation

Enforce a minimum finality level on the contract side, not the caller side:

1. **EVM chains**: Reject requests with `finality: Latest` in `verify_foreign_transaction`. Require at minimum `Safe` or `Finalized` depending on the chain's reorg risk profile.
2. **Bitcoin**: Enforce a protocol-level minimum `confirmations` floor (e.g., `>= 6`) in the contract before enqueuing the request. Do not rely on the caller to supply a safe value.
3. Store the enforced minimum per-chain in the on-chain `ChainEntry` (alongside `quorum`) so participants can vote on the minimum finality requirement for each chain, analogous to how `quorum` is voted in.

---

### Proof of Concept

**EVM (Abstract chain) — `Latest` finality:**

```json
{
  "request": {
    "request": {
      "Abstract": {
        "tx_id": "<attacker_tx_id>",
        "extractors": ["BlockHash"],
        "finality": "Latest"
      }
    },
    "domain_id": 3,
    "payload_version": 1
  }
}
```

Call `verify_foreign_transaction` with 1 yoctoNEAR attached. The contract accepts it (no finality floor check). MPC nodes query the RPC with `FinalityTag::Latest`, find the transaction in the latest block, extract the block hash, and produce a threshold signature. The attacker submits this signature to the NEAR bridge contract before the block is reorged, claiming bridge funds. The Ethereum transaction is then reorged, returning the ETH to the attacker.

**Bitcoin — `confirmations: 0`:**

```json
{
  "request": {
    "request": {
      "Bitcoin": {
        "tx_id": "<attacker_tx_id>",
        "confirmations": 0,
        "extractors": ["BlockHash"]
      }
    },
    "domain_id": 3,
    "payload_version": 1
  }
}
```

The Bitcoin inspector check `0 <= transaction_block_confirmation` passes for any transaction, including unconfirmed ones. The attacker obtains an MPC signature for an unconfirmed transaction and double-spends the UTXO on Bitcoin after claiming the NEAR-side tokens. [6](#0-5) [7](#0-6) [3](#0-2) [8](#0-7)

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L117-175)
```rust
    async fn execute_foreign_chain_request(
        &self,
        request: &dtos::ForeignChainRpcRequest,
        payload_version: dtos::ForeignTxPayloadVersion,
    ) -> anyhow::Result<dtos::ForeignTxSignPayload> {
        chain_is_supported(&self.foreign_chain_policy_reader, request).await?;

        let values: Vec<dtos::ExtractedValue> = match request {
            dtos::ForeignChainRpcRequest::Ethereum(_request) => {
                bail!("ForeignChainRpcRequest::Ethereum is unsupported")
            }
            dtos::ForeignChainRpcRequest::Solana(_request) => {
                bail!("ForeignChainRpcRequest::Solana is unsupported")
            }
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
            }
            dtos::ForeignChainRpcRequest::Abstract(request) => {
                let inspector = self
                    .inspectors
                    .abstract_chain
                    .as_ref()
                    .context("no inspector configured for abstract")?;

                let transaction_id = request.tx_id.0.into();
                let finality: EthereumFinality = request.finality.clone().try_into()?;
                let extractors: Vec<AbstractExtractor> = request
                    .extractors
                    .iter()
                    .cloned()
                    .map(TryInto::try_into)
                    .collect::<Result<_, _>>()?;
                let values = inspector
                    .extract(transaction_id, finality, extractors)
                    .timeout(FOREIGN_CHAIN_INSPECTION_TIMEOUT)
                    .await
                    .context("timed out during execution of foreign chain request")??;
                values.into_iter().map(Into::into).collect()
            }
            dtos::ForeignChainRpcRequest::Bnb(request) => {
                let inspector = self
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L337-347)
```rust
        let payload = match payload_version {
            dtos::ForeignTxPayloadVersion::V1 => {
                dtos::ForeignTxSignPayload::V1(dtos::ForeignTxSignPayloadV1 {
                    request: request.clone(),
                    values,
                })
            }
            _ => bail!("unsupported payload_version"),
        };
        Ok(payload)
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
