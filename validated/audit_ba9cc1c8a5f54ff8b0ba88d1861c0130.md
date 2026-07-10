### Title
Caller-Controlled Finality Level Accepted Without Minimum Enforcement Enables Reorg-Based Forged Foreign-Chain Attestation - (File: `crates/node/src/providers/verify_foreign_tx/sign.rs`, `crates/foreign-chain-inspector/src/evm/inspector.rs`, `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

The `verify_foreign_transaction` flow accepts a caller-supplied `finality` field (e.g., `EvmFinality::Latest`, `SolanaFinality::Processed`, `BlockConfirmations(1)`) without any minimum finality enforcement at the contract or node level. This is the direct analog of the GMX report: just as GMXAdapter blindly trusted GMX's manipulable price instead of cross-checking against Chainlink, the NEAR MPC network blindly trusts the caller-specified finality level instead of enforcing a safe minimum. An unprivileged caller can obtain a valid MPC threshold signature attesting to a foreign-chain transaction that has not reached economic finality, enabling reorg-based double-spend or invalid bridge execution.

---

### Finding Description

The `EvmRpcRequest` struct exposes a `finality` field that is fully caller-controlled: [1](#0-0) 

The `EvmFinality` enum includes `Latest` as a valid variant: [2](#0-1) 

Similarly, `SolanaFinality::Processed` is accepted: [3](#0-2) 

The on-chain `verify_foreign_transaction` entry point performs no validation of the finality level — it only checks that the chain is supported and the domain purpose is `ForeignTx`: [4](#0-3) 

The node's `execute_foreign_chain_request` passes the caller-specified finality directly to the inspector without any minimum enforcement: [5](#0-4) 

The EVM inspector's `verify_finality_level` faithfully uses `FinalityTag::Latest` when `EthereumFinality::Latest` is requested — it does not reject it or substitute a safer level: [6](#0-5) 

The signed payload (`ForeignTxSignPayloadV1`) includes the full request (with the weak finality field) and the extracted values: [7](#0-6) 

The threshold signature is produced over `SHA-256(borsh(ForeignTxSignPayload))`, which commits to the finality level. However, no party in the signing pipeline — neither the contract nor any MPC node — rejects or overrides a caller-supplied `Latest` or `Processed` finality before signing.

---

### Impact Explanation

**Impact: High — Forged foreign-chain verification / light-client-style verification bypass causing invalid bridge execution or double-spend.**

An attacker obtains a valid MPC threshold signature attesting that a foreign-chain transaction (e.g., an EVM deposit into a bridge) was observed at `Latest` finality. The signed payload is cryptographically valid and carries the full weight of the MPC network's key. If the target block is subsequently reorganized (naturally or via a deliberate reorg attack), the transaction no longer exists on the canonical chain, but the MPC attestation remains valid and usable. A bridge contract that does not independently enforce a minimum finality level on the signed payload will process the attestation and release funds for a deposit that was rolled back — a direct double-spend.

Even for bridge contracts that do check the finality field in the payload, the MPC network's willingness to sign `Latest`-finality attestations undermines the trust model: the network is supposed to be a trusted verifier, but it produces signatures that are weaker than the security guarantees the bridge relies on.

---

### Likelihood Explanation

**Likelihood: Medium.**

- The entry path is fully unprivileged: any NEAR account can call `verify_foreign_transaction` with `EvmFinality::Latest` and a 1-yoctoNEAR deposit.
- EVM chains (Ethereum, BNB, Polygon, Base, Arbitrum, Abstract, HyperEVM) all support `Latest` finality in the enum and the inspector.
- Ethereum mainnet reorgs of 1–2 blocks occur occasionally; for chains with weaker consensus (BNB, Polygon PoS), reorgs are more frequent.
- For Bitcoin, `BlockConfirmations(1)` is accepted — a single-confirmation Bitcoin transaction is well-known to be double-spendable.
- The attacker does not need to control any MPC node or exceed the signing threshold; they only need to submit a well-formed request and wait for the MPC network to sign it.

---

### Recommendation

Enforce a minimum finality level at the contract or node layer, not at the caller's discretion:

1. **Contract-level enforcement**: In `verify_foreign_transaction`, reject requests where `EvmFinality::Latest` or `SolanaFinality::Processed` is specified. Only `Finalized` (and optionally `Safe`) should be accepted for bridge-security use cases.
2. **Node-level enforcement**: In `execute_foreign_chain_request`, assert that the finality level meets a configured minimum before proceeding with the RPC call and signing.
3. **Per-chain configuration**: Store the minimum acceptable finality per chain in the on-chain `ForeignChainRpcWhitelist` / `ChainEntry`, voted in by node operators, so the policy is governed on-chain rather than left to callers.

This mirrors the fix described in the external report: just as the GMX fix replaced the manipulable GMX price with the Chainlink price, the fix here replaces the caller-controlled finality with a protocol-enforced minimum.

---

### Proof of Concept

1. Attacker deploys a NEAR contract that calls `verify_foreign_transaction` with:
   ```json
   {
     "request": {
       "Abstract": {
         "tx_id": "<attacker_controlled_evm_tx_hash>",
         "finality": "Latest",
         "extractors": [{"Log": {"log_index": 0}}]
       }
     },
     "domain_id": <foreign_tx_domain_id>,
     "payload_version": "V1"
   }
   ```
   with a 1-yoctoNEAR deposit and sufficient gas.

2. The contract accepts the request (no finality validation at `crates/contract/src/lib.rs:526-542`).

3. MPC nodes each call `execute_foreign_chain_request`, which passes `EthereumFinality::Latest` to the EVM inspector (`crates/node/src/providers/verify_foreign_tx/sign.rs:160`).

4. The EVM inspector calls `verify_finality_level` with `FinalityTag::Latest` (`crates/foreign-chain-inspector/src/evm/inspector.rs:107-111`), which succeeds as long as the latest block number ≥ the transaction's block number — a trivially satisfied condition.

5. The MPC network produces a threshold ECDSA signature over `SHA-256(borsh(ForeignTxSignPayloadV1 { request: Abstract(EvmRpcRequest { tx_id, finality: Latest, ... }), values: [Log(...)] }))`.

6. Attacker receives the `VerifyForeignTransactionResponse` containing a valid MPC signature.

7. Attacker submits the signed attestation to a bridge contract on NEAR to claim funds for the EVM deposit.

8. Attacker (or a natural reorg) removes the EVM transaction from the canonical chain.

9. The bridge has released funds for a deposit that no longer exists on the foreign chain — double-spend complete.

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L221-225)
```rust
pub struct EvmRpcRequest {
    pub tx_id: EvmTxId,
    pub extractors: Vec<EvmExtractor>,
    pub finality: EvmFinality,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L768-772)
```rust
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L792-796)
```rust
pub enum SolanaFinality {
    Processed,
    Confirmed,
    Finalized,
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L117-173)
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
