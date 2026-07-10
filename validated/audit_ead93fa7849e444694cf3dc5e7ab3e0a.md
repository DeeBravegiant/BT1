### Title
Caller-Controlled Finality Parameters Allow MPC Attestation of Unfinalized Foreign-Chain Transactions - (File: `crates/contract/src/lib.rs`, `crates/node/src/providers/verify_foreign_tx/sign.rs`)

---

### Summary

The `verify_foreign_transaction` instruction accepts user-supplied finality parameters (`confirmations` for Bitcoin, `finality` for EVM chains) without enforcing any minimum safety threshold at the contract or node level. An unprivileged caller can set `confirmations: 0` or `finality: Latest`, causing the MPC network to produce a threshold signature attesting to a foreign-chain transaction that has not reached safe finality. A bridge contract consuming this signature can be drained via a double-spend before the underlying transaction is reversed.

---

### Finding Description

The `verify_foreign_transaction` public method on the MPC contract accepts a `VerifyForeignTransactionRequestArgs` struct that embeds chain-specific finality parameters directly controlled by the caller.

For Bitcoin, the parameter is `BitcoinRpcRequest.confirmations: BlockConfirmations`, which is a plain `u64` wrapper with no lower bound enforced anywhere: [1](#0-0) 

The ABI schema explicitly allows `minimum: 0.0`: [2](#0-1) 

The contract's `verify_foreign_transaction` method performs no validation of the `confirmations` or `finality` fields — it only checks domain existence, gas, deposit, and chain support: [3](#0-2) 

The node's `execute_foreign_chain_request` passes the caller-supplied value directly to the inspector as the threshold: [4](#0-3) 

The `BitcoinInspector` enforces only that the actual on-chain confirmations meet the caller-supplied threshold. When the threshold is `0`, the condition `0 <= actual_confirmations` is trivially true for any transaction visible to the RPC node, including one-confirmation transactions: [5](#0-4) 

For EVM chains (Abstract, BNB, Base, Arbitrum, HyperEVM, Polygon), the caller supplies `EvmFinality::Latest`, which is a valid enum variant accepted without restriction: [6](#0-5) 

The node converts it directly to `FinalityTag::Latest` and queries the chain tip, providing no reorg protection: [7](#0-6) 

---

### Impact Explanation

A bridge contract (e.g., Omnibridge inbound flow) that calls `verify_foreign_transaction` and uses the returned MPC signature to release NEAR-side assets will release funds upon receiving a valid threshold signature. Because the MPC network signed an attestation for a transaction with zero or one confirmation, the underlying foreign-chain transaction can be reversed (double-spent on Bitcoin, reorged on EVM chains). The attacker receives NEAR-side assets and recovers the foreign-chain funds, constituting a direct theft of bridge funds.

This maps to the allowed impact: **High — forged foreign-chain verification / light-client-style verification bypass that causes invalid bridge execution or double-spend conditions.**

---

### Likelihood Explanation

The attack requires only a standard NEAR account and 1 yoctoNEAR deposit. No privileged access, threshold collusion, or TEE bypass is needed. The attacker directly controls the `confirmations` or `finality` field in the public `verify_foreign_transaction` call. For Bitcoin, a 1-confirmation double-spend requires meaningful hashrate but is economically rational against a high-value bridge. For EVM chains, `Latest` finality is reversible by any reorg, which occurs naturally on chains like Polygon and BNB.

---

### Recommendation

Enforce a minimum finality floor at the contract level inside `verify_foreign_transaction`, before the request is enqueued:

- **Bitcoin**: Reject any `BitcoinRpcRequest` where `confirmations < MINIMUM_BITCOIN_CONFIRMATIONS` (e.g., 6). This constant should be stored in the on-chain config and updatable by governance vote.
- **EVM chains**: Reject any `EvmRpcRequest` where `finality == EvmFinality::Latest`. Only `Safe` or `Finalized` should be accepted for bridge-security use cases.
- **Starknet**: Reject `StarknetFinality::AcceptedOnL2`; require `AcceptedOnL1`.

These checks are the direct analog of slippage guards: they prevent the caller from weakening the safety invariant that the MPC attestation is supposed to enforce.

---

### Proof of Concept

1. Attacker submits a Bitcoin transaction `T` to a bridge address, sending 1 BTC.
2. `T` is mined into block `B` (1 confirmation).
3. Attacker immediately calls `verify_foreign_transaction` on the MPC contract with:
   ```json
   {
     "request": {
       "Bitcoin": {
         "tx_id": "<T>",
         "confirmations": 0,
         "extractors": ["BlockHash"]
       }
     },
     "domain_id": <foreign_tx_domain>,
     "payload_version": 1
   }
   ```
4. The contract accepts the request (no confirmation floor check).
5. MPC nodes call `BitcoinInspector::extract(T, threshold=0, ...)`. The check `0 <= 1` passes. The canonical-chain check passes (block `B` is canonical at this moment). The MPC network produces a threshold signature over `(request, BlockHash(B))`.
6. Attacker submits the MPC signature to the bridge contract on NEAR, which releases the equivalent NEAR-side assets.
7. Attacker broadcasts a conflicting Bitcoin transaction double-spending `T`'s inputs. With 1 confirmation, a well-resourced attacker (or a chain with fast blocks like BNB/Polygon using `Latest`) can succeed.
8. Attacker retains both the NEAR-side assets and the original Bitcoin funds.

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1282-1282)
```rust
pub struct BlockConfirmations(pub u64);
```

**File:** crates/contract/tests/snapshots/abi__abi_has_not_changed.snap (L2516-2520)
```text
        "BlockConfirmations": {
          "type": "integer",
          "format": "uint64",
          "minimum": 0.0
        },
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
