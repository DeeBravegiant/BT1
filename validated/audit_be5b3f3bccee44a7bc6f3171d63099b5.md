### Title
`EvmFinality::Latest` Accepted for Arbitrum with Trivially-Passing Finality Check Enables MPC Signature Over Non-L1-Confirmed Transactions — (`crates/foreign-chain-inspector/src/evm/inspector.rs`)

---

### Summary

An unprivileged caller can submit `ForeignChainRpcRequest::Arbitrum` with `finality=EvmFinality::Latest`. The shared `EvmInspector::verify_finality_level` implementation makes this check a no-op (always passes), and `verify_block_is_canonical` passes at query time. The MPC network then produces a valid threshold signature over a `ForeignTxSignPayload` that commits to a non-L1-confirmed Arbitrum block. If the Arbitrum sequencer reorganizes or delays that block before L1 batch confirmation, the signed payload remains cryptographically valid while the attested transaction is no longer canonical, enabling a double-spend against any bridge contract that trusts the MPC attestation.

---

### Finding Description

**Entrypoint**: Any unprivileged account calls `verify_foreign_transaction` with:
```
ForeignChainRpcRequest::Arbitrum(EvmRpcRequest {
    tx_id: <target_tx>,
    finality: EvmFinality::Latest,
    extractors: vec![EvmExtractor::BlockHash],
})
```

**Node-side dispatch** (`crates/node/src/providers/verify_foreign_tx/sign.rs`, line 226):
```rust
let finality: EthereumFinality = request.finality.clone().try_into()?;
```
The conversion in `crates/foreign-chain-inspector/src/contract_interface_conversions.rs` maps `EvmFinality::Latest → EthereumFinality::Latest` without any chain-specific guard. [1](#0-0) 

**`verify_finality_level` with `Latest`** (`crates/foreign-chain-inspector/src/evm/inspector.rs`, lines 102–125):
```rust
EthereumFinality::Latest => FinalityTag::Latest,
// ...
if head.number < receipt_block_number {
    return Err(ForeignChainInspectionError::NotFinalized);
}
```
`eth_getBlockByNumber("latest")` returns the sequencer's current head. Because the receipt was already fetched successfully, `head.number >= receipt_block_number` is **always true**. The check is a no-op. [2](#0-1) 

**`verify_block_is_canonical`** (`crates/foreign-chain-inspector/src/evm/inspector.rs`, lines 135–159):
This re-fetches the block by number and compares hashes. At query time the block is canonical, so it passes. No subsequent re-check occurs. [3](#0-2) 

**Signing**: The node builds and signs `ForeignTxSignPayload::V1 { request, values }` where `request` contains `finality: Latest` and `values` contains the block hash observed at query time. [4](#0-3) 

**`respond_verify_foreign_tx`** only verifies the ECDSA signature against `payload_hash`. It does **not** re-verify block canonicality. [5](#0-4) 

**Arbitrum-specific risk**: Arbitrum's "latest" tag resolves to the sequencer's head, which is **not L1-confirmed**. The sequencer can reorganize or delay posting a batch to L1 for up to the challenge window (~1 week). A block that was canonical at MPC query time may not appear in the final L1-posted batch.

There is no Arbitrum-specific minimum finality enforcement anywhere in the codebase — the `ArbitrumInspector` is a plain type alias for the generic `EvmInspector<Client, Arbitrum>` with no overrides. [6](#0-5) 

---

### Impact Explanation

The MPC network produces a valid threshold signature over a `ForeignTxSignPayload` that commits to:
- `tx_id` of the target transaction
- `finality: Latest` (non-L1-confirmed)
- The block hash observed at query time

If the Arbitrum sequencer reorganizes that block before L1 batch confirmation, the transaction may not exist on the canonical chain, but the MPC signature remains cryptographically valid. A bridge contract that trusts the MPC attestation (without independently enforcing `finality >= Safe/Finalized`) would release funds for a transaction that is no longer canonical — a double-spend.

---

### Likelihood Explanation

- **Attacker control**: Any unprivileged account can submit the request; no special privilege required.
- **Arbitrum sequencer**: The sequencer is centralized (Offchain Labs). While outright malice is unlikely, sequencer bugs, delayed L1 posting, or a compromised sequencer are realistic threat vectors within the challenge window.
- **Window**: The gap between sequencer head and L1 confirmation is up to ~1 week, giving ample time to exploit a signed-but-unconfirmed block.
- **No existing guard**: There is no code anywhere in the production path that rejects `EvmFinality::Latest` for Arbitrum.

---

### Recommendation

1. **Enforce a minimum finality level for Arbitrum** in the node-side dispatch or in the `ArbitrumInspector`. Reject `EthereumFinality::Latest` (and optionally `Safe`) for Arbitrum, requiring at least `Finalized` (which on Arbitrum corresponds to L1-confirmed batches).
2. **Add a chain-specific finality guard** in `crates/node/src/providers/verify_foreign_tx/sign.rs` before calling `inspector.extract(...)`:
   ```rust
   dtos::ForeignChainRpcRequest::Arbitrum(request) => {
       let finality: EthereumFinality = request.finality.clone().try_into()?;
       if finality != EthereumFinality::Finalized {
           bail!("Arbitrum requires Finalized finality");
       }
       // ...
   }
   ```
3. Alternatively, enforce this at the contract level by rejecting `EvmFinality::Latest` for Arbitrum in `verify_foreign_transaction`.

---

### Proof of Concept

A deterministic mock-RPC unit test can demonstrate this:

1. Serve `eth_getTransactionReceipt` → receipt at block N, hash `H1`.
2. Serve `eth_getBlockByNumber("latest")` → block N (head.number == N, so `verify_finality_level` passes).
3. Serve `eth_getBlockByNumber(N)` → block N with hash `H1` (canonical at query time, so `verify_block_is_canonical` passes).
4. The inspector returns `EvmExtractedValue::BlockHash(H1)`.
5. The node builds `ForeignTxSignPayload::V1 { request: Arbitrum(finality=Latest, tx_id=T), values: [BlockHash(H1)] }` and produces a valid MPC signature `σ`.
6. Now serve `eth_getBlockByNumber(N)` → block N with hash `H2 ≠ H1` (simulating a reorg).
7. Call `respond_verify_foreign_tx(request, response { payload_hash, σ })` — it **accepts** the signature because it only verifies `σ` against `payload_hash`; it does not re-check canonicality.
8. The bridge contract receives a valid MPC signature over a transaction whose block is no longer canonical. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/foreign-chain-inspector/src/contract_interface_conversions.rs (L44-55)
```rust
impl TryFrom<dtos::EvmFinality> for EthereumFinality {
    type Error = ConversionError;
    fn try_from(value: dtos::EvmFinality) -> Result<Self, Self::Error> {
        match value {
            dtos::EvmFinality::Finalized => Ok(EthereumFinality::Finalized),
            dtos::EvmFinality::Safe => Ok(EthereumFinality::Safe),
            dtos::EvmFinality::Latest => Ok(EthereumFinality::Latest),
            _ => Err(ConversionError::UnsupportedVariant {
                context: "EvmFinality",
            }),
        }
    }
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L50-85)
```rust
    async fn extract(
        &self,
        transaction: Chain::TransactionHash,
        finality: EthereumFinality,
        extractors: Vec<EvmExtractor>,
    ) -> Result<Vec<EvmExtractedValue<Chain>>, ForeignChainInspectionError> {
        let get_transaction_receipt_args = GetTransactionReceiptARgs {
            transaction_hash: H256(transaction.into()),
        };
        let transaction_receipt: GetTransactionReceiptResponse = self
            .client
            .request(
                GET_TRANSACTION_RECEIPT_METHOD,
                &get_transaction_receipt_args,
            )
            .await?;

        self.verify_finality_level(transaction_receipt.block_number, finality)
            .await?;
        self.verify_block_is_canonical(
            transaction_receipt.block_number,
            transaction_receipt.block_hash,
        )
        .await?;

        let transaction_success = transaction_receipt.status == U64::one();

        if !transaction_success {
            return Err(ForeignChainInspectionError::TransactionFailed);
        }

        extractors
            .iter()
            .map(|extractor| extractor.extract_value(&transaction_receipt))
            .collect()
    }
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L107-124)
```rust
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

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L135-159)
```rust
    async fn verify_block_is_canonical(
        &self,
        receipt_block_number: U64,
        receipt_block_hash: H256,
    ) -> Result<(), ForeignChainInspectionError> {
        let args = GetBlockByNumberArgs::new(
            BlockNumberOrTag::Number(receipt_block_number),
            ReturnFullTransactionObjects::from(false),
        );
        let canonical: GetBlockByNumberResponse = self
            .client
            .request(GET_BLOCK_BY_NUMBER_METHOD, &args)
            .await?;

        let hash_matches = canonical.hash == receipt_block_hash;
        let height_matches = canonical.number == receipt_block_number;
        if !hash_matches || !height_matches {
            return Err(ForeignChainInspectionError::NonCanonicalBlock {
                block_number: receipt_block_number.as_u64(),
                receipt_hash: receipt_block_hash.into(),
                canonical_hash: canonical.hash.into(),
            });
        }
        Ok(())
    }
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1509)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}

impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/contract/src/lib.rs (L691-754)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/foreign-chain-inspector/src/arbitrum/inspector.rs (L14-16)
```rust
pub type ArbitrumInspector<Client> = crate::evm::inspector::EvmInspector<Client, Arbitrum>;
pub type ArbitrumExtractedValue = crate::evm::inspector::EvmExtractedValue<Arbitrum>;
pub type ArbitrumExtractor = crate::evm::inspector::EvmExtractor;
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L218-239)
```rust
            dtos::ForeignChainRpcRequest::Arbitrum(request) => {
                let inspector = self
                    .inspectors
                    .arbitrum
                    .as_ref()
                    .context("no inspector configured for Arbitrum")?;

                let transaction_id = request.tx_id.0.into();
                let finality: EthereumFinality = request.finality.clone().try_into()?;
                let extractors: Vec<ArbitrumExtractor> = request
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
```
