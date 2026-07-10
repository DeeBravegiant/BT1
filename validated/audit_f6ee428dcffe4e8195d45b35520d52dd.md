### Title
Caller-Controlled Finality Depth Accepted Without Minimum Enforcement Enables Signed Attestation of Unfinalized Foreign Transactions - (File: `crates/foreign-chain-inspector/src/evm/inspector.rs`, `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

---

### Summary

An unprivileged caller can submit a `verify_foreign_transaction` request with `EvmFinality::Latest` (for EVM chains) or `BlockConfirmations(0)` (for Bitcoin) and receive a valid MPC threshold signature attesting to a foreign-chain transaction that has not reached sufficient finality. Because neither the on-chain contract nor the MPC nodes enforce a minimum finality depth, the signed attestation can be used to trigger bridge actions for a transaction that is subsequently reorganized away, enabling double-spend or theft of bridged funds.

---

### Finding Description

The `verify_foreign_transaction` flow is the NEAR MPC analog of the `minBlockHeight` vulnerability. Just as the in3-server allowed nodes to sign block hashes for blocks that might be reorganized by accepting an insecure `minBlockHeight`, the NEAR MPC system allows any caller to request and receive a threshold signature attesting to a foreign-chain transaction at an insecure finality level.

**Root cause 1 — EVM chains (`EvmFinality::Latest` accepted without restriction):**

`EvmFinality` is defined with three variants, including `Latest`: [1](#0-0) 

The on-chain `verify_foreign_transaction` method performs no validation of the `finality` field inside `EvmRpcRequest`: [2](#0-1) 

The MPC node's `execute_foreign_chain_request` converts the caller-supplied `EvmFinality` directly into `EthereumFinality` and passes it to the inspector: [3](#0-2) 

Inside `verify_finality_level`, `EthereumFinality::Latest` maps to `FinalityTag::Latest`, which queries `eth_getBlockByNumber("latest")`. The latest block number is always `>=` the receipt's block number (since the receipt exists), so the check `head.number < receipt_block_number` always passes: [4](#0-3) 

The MPC network then signs the attestation for a transaction that is only in the latest (non-finalized) block, subject to reorg.

**Root cause 2 — Bitcoin (`BlockConfirmations(0)` accepted without restriction):**

The Bitcoin inspector enforces the caller-supplied threshold with a simple `<=` comparison: [5](#0-4) 

With `block_confirmations_threshold = BlockConfirmations(0)`, the condition `0 <= any_value` is always true, so any transaction — including one with zero confirmations (unconfirmed, in the mempool) — passes the check. There is no minimum enforced anywhere in the contract or node.

---

### Impact Explanation

The MPC network produces a threshold signature over a `ForeignTxSignPayload` that encodes the request (including the insecure finality level) and the extracted values (e.g., block hash). This signed attestation is returned to the caller and can be presented to a bridge contract (e.g., Omnibridge inbound flow) to claim funds on NEAR.

If the underlying foreign-chain transaction is subsequently reorganized:
- The signed attestation remains cryptographically valid (it was signed over the block hash at the time of signing).
- A bridge contract that trusts MPC signatures will accept the attestation and release funds.
- The attacker has received bridged funds for a transaction that no longer exists on the canonical chain — a direct double-spend.

This matches the **High** impact category: cross-chain verification bypass that causes invalid bridge execution or double-spend conditions.

---

### Likelihood Explanation

- Any unprivileged caller can submit `verify_foreign_transaction` with `EvmFinality::Latest` or `BlockConfirmations(0)` — no special role or collusion is required.
- The attack requires only: (1) submit a transaction on the foreign chain, (2) immediately call `verify_foreign_transaction` before finality, (3) receive the MPC signature, (4) trigger a reorg (or wait for a natural one on chains with frequent shallow reorgs such as BNB, Polygon, or pre-Merge EVM chains), (5) present the signature to the bridge.
- On chains with frequent shallow reorgs (BNB, Polygon, HyperEVM), natural reorgs make this exploitable without any active attack on the chain.
- The `EvmFinality::Latest` variant is explicitly present in the public ABI and documented as a valid option.

---

### Recommendation

1. **Enforce a per-chain minimum finality depth on-chain.** The `verify_foreign_transaction` contract method should reject requests where the supplied finality is below a chain-specific safe minimum (e.g., reject `EvmFinality::Latest` for all EVM chains; enforce `BlockConfirmations >= N` for Bitcoin where `N` is chain-specific).
2. **Enforce the minimum in the MPC node as a defense-in-depth.** `execute_foreign_chain_request` should validate the finality parameter against a per-chain safe minimum before querying the RPC, and refuse to sign if the minimum is not met.
3. **Remove or restrict `EvmFinality::Latest`.** If `Latest` has no legitimate use case for bridge security, remove it from the accepted enum variants or gate it behind a separate, explicitly unsafe flag that is rejected by the contract.
4. **Enforce `BlockConfirmations >= 1` as an absolute minimum for Bitcoin**, and document a recommended safe minimum (e.g., 6 confirmations) per chain.

---

### Proof of Concept

```
1. Attacker submits a transaction T on BNB chain (or any EVM chain with shallow reorgs).
2. Attacker immediately calls verify_foreign_transaction on the NEAR MPC contract:
   {
     request: ForeignChainRpcRequest::Bnb(EvmRpcRequest {
       tx_id: T,
       extractors: [BlockHash],
       finality: EvmFinality::Latest,   // ← insecure, accepted without restriction
     }),
     domain_id: <foreign_tx_domain>,
     payload_version: V1,
   }
3. MPC nodes call eth_getTransactionReceipt(T) → receipt at block B.
4. MPC nodes call eth_getBlockByNumber("latest") → latest block L >= B → finality check passes.
5. MPC nodes call eth_getBlockByNumber(B) → canonical check passes (T is still canonical).
6. MPC nodes produce threshold signature over (request, [block_hash_of_B]).
7. Attacker receives valid MPC-signed attestation.
8. A natural or induced reorg removes block B; T no longer exists on the canonical chain.
9. Attacker presents the MPC-signed attestation to the Omnibridge contract on NEAR.
10. Bridge releases funds for a transaction that no longer exists → double-spend.

For Bitcoin with BlockConfirmations(0):
   confirmations: BlockConfirmations(0)
   → check: 0 <= rpc_response.confirmations (always true, even for unconfirmed tx)
   → MPC signs attestation for a mempool transaction that may never confirm.
```

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L767-772)
```rust
#[non_exhaustive]
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L159-172)
```rust
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
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L102-125)
```rust
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
