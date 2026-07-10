### Title
User-Controlled Finality Depth Allows Reorg-Based Double-Spend via `verify_foreign_transaction` - (File: `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`, `crates/foreign-chain-inspector/src/evm/inspector.rs`)

---

### Summary

The `verify_foreign_transaction` flow accepts a user-supplied finality depth (`confirmations` for Bitcoin, `finality: Latest/Safe/Finalized` for EVM chains) with no minimum enforced by either the contract or the inspectors. An attacker can request an MPC attestation for a foreign-chain transaction that has only 1 confirmation (Bitcoin) or is merely in the latest block (EVM), obtain a valid threshold signature, use it to claim funds on NEAR, and then cause a chain reorganization to reclaim the original deposit — a double-spend.

---

### Finding Description

The `verify_foreign_transaction` contract method accepts a `VerifyForeignTransactionRequestArgs` that includes a chain-specific RPC request. For Bitcoin, the request carries a `confirmations: BlockConfirmations` field; for EVM chains, it carries a `finality: EvmFinality` field. Neither the contract nor the inspectors enforce a minimum value.

**Contract entry point** — no minimum confirmation/finality check:

```rust
// crates/contract/src/lib.rs ~line 519
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    // Only checks: chain is supported, domain purpose, gas, deposit.
    // No minimum confirmations or finality level enforced.
    ...
    self.enqueue_yield_request(...);
}
```

**Bitcoin inspector** — threshold is taken directly from the user's request:

```rust
// crates/foreign-chain-inspector/src/bitcoin/inspector.rs lines 50-58
let transaction_block_confirmation = rpc_response.confirmations.into();
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;

if !enough_block_confirmations {
    return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { ... });
}
```

`block_confirmations_threshold` is the value the caller placed in `BitcoinRpcRequest { confirmations, ... }`. Setting it to `1` (or `0`) passes the check for any recently-mined transaction.

**EVM inspector** — `Latest` finality is a supported, accepted variant:

```rust
// crates/foreign-chain-inspector/src/evm/inspector.rs lines 107-123
let finality_tag = match finality {
    EthereumFinality::Finalized => FinalityTag::Finalized,
    EthereumFinality::Safe     => FinalityTag::Safe,
    EthereumFinality::Latest   => FinalityTag::Latest,   // ← accepted
};
// head.number >= receipt_block_number is the only check
```

A transaction in the very latest block passes with `finality: Latest`.

**Signed payload** — the signed artifact contains only `(request, values)`, not a timestamp or block-depth proof:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs lines 1499-1502
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,   // includes user-chosen confirmations/finality
    pub values: Vec<ExtractedValue>,
}
```

The threshold signature therefore attests to a state that was observed at an arbitrarily shallow finality depth chosen by the attacker.

---

### Impact Explanation

**High — forged foreign-chain verification enabling double-spend.**

An attacker who controls a bridge or omnibridge inbound flow can:

1. Deposit funds on Bitcoin (or an EVM chain).
2. Immediately call `verify_foreign_transaction` with `confirmations: 1` (Bitcoin) or `finality: Latest` (EVM).
3. The MPC network observes the transaction (1 confirmation is sufficient), runs the threshold signing protocol, and returns a valid `VerifyForeignTransactionResponse` containing a threshold signature over `(request, block_hash)`.
4. The attacker submits this signature to the NEAR bridge contract to claim the bridged funds.
5. The attacker then mines a competing Bitcoin/EVM fork that excludes the original deposit transaction (a reorg), recovering the deposited funds on the foreign chain.
6. Net result: funds claimed on NEAR + original deposit recovered on the foreign chain — a double-spend.

The MPC signature is cryptographically valid and was produced by an honest threshold of nodes; the vulnerability is that the observation window (finality depth) was too shallow to be reorg-resistant, exactly as the TWAP oracle in the reference report captured a price from a 1-block window.

---

### Likelihood Explanation

Bitcoin 1-confirmation reorgs are historically common (selfish mining, natural orphans). EVM `Latest`-finality reorgs are trivial on chains with short block times. The attacker controls the `confirmations` / `finality` field in the request — no privileged access is required. Any unprivileged caller of `verify_foreign_transaction` can trigger this path.

---

### Recommendation

1. **Enforce a minimum finality depth on-chain.** Add a per-chain minimum to the `ChainEntry` stored in `foreign_chain_rpc_whitelist` (already voted in by threshold participants). Reject any `verify_foreign_transaction` request whose `confirmations` or `finality` is below the chain's minimum.

2. **Disallow `EvmFinality::Latest` for production bridge use.** Either remove the `Latest` variant from the accepted set in `verify_finality_level`, or gate it behind an explicit per-chain allowlist flag.

3. **Include the observed block height/hash and confirmation depth in the signed payload** (`ForeignTxSignPayloadV1`) so downstream verifiers can independently assess finality adequacy.

---

### Proof of Concept

```
Attacker (unprivileged NEAR account):

1. Sends 1 BTC to bridge deposit address on Bitcoin mainnet.
   → tx_id = 0xABCD..., included in block N (1 confirmation).

2. Calls verify_foreign_transaction({
       request: Bitcoin({ tx_id: 0xABCD, confirmations: 1, extractors: [BlockHash] }),
       domain_id: <ForeignTx domain>,
       payload_version: V1,
   })
   → Contract enqueues yield request (no minimum-confirmation check).

3. MPC nodes call BitcoinInspector::extract(tx_id, threshold=1, [BlockHash]).
   → block_confirmations_threshold(1) <= transaction_block_confirmation(1) → passes.
   → verify_block_is_canonical succeeds (block N is canonical at this moment).
   → Threshold signing produces valid signature over SHA-256(borsh(ForeignTxSignPayloadV1{
         request: Bitcoin({tx_id:0xABCD, confirmations:1, ...}),
         values: [BlockHash(block_N_hash)],
     })).

4. Attacker receives VerifyForeignTransactionResponse { payload_hash, signature }.
   → Submits to NEAR bridge contract → bridge releases funds on NEAR.

5. Attacker mines a Bitcoin reorg that excludes block N (replaces with block N').
   → Original deposit tx 0xABCD no longer exists on canonical Bitcoin chain.
   → Attacker recovers the 1 BTC on Bitcoin.

Result: 1 BTC claimed on NEAR + 1 BTC recovered on Bitcoin = double-spend.
```

**Root cause files:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1502)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
```
