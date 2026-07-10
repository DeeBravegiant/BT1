### Title
User-Controlled `EvmFinality::Latest` Allows MPC Attestation of Reorg-able Transactions — (`crates/foreign-chain-inspector/src/evm/inspector.rs`, `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint accepts a caller-supplied `EvmFinality` field in `EvmRpcRequest`. The contract performs no minimum-finality enforcement. When a caller supplies `EvmFinality::Latest`, MPC nodes verify the transaction against the most recent (reorg-able) EVM block — the direct analog of Uniswap's `slot0` — and produce a valid threshold signature attesting to a transaction that may subsequently be rolled back. This enables a double-spend on any bridge flow that relies on the MPC attestation.

---

### Finding Description

`EvmRpcRequest` carries a caller-controlled `finality` field:

```rust
pub struct EvmRpcRequest {
    pub tx_id: EvmTxId,
    pub extractors: Vec<EvmExtractor>,
    pub finality: EvmFinality,   // ← fully caller-controlled
}
``` [1](#0-0) 

The enum includes `Latest` as a valid, accepted variant:

```rust
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}
``` [2](#0-1) 

`verify_foreign_transaction` in the contract calls `check_request_preconditions` (which validates domain, gas, deposit, and TEE state) and then checks whether the chain is in the supported set — but **never validates the finality level**:

```rust
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    self.check_request_preconditions(...);
    let requested_chain = request.request.chain();
    let supported_chains = self.get_supported_foreign_chains();
    if !supported_chains.contains(&requested_chain) { ... }
    // ← no finality check; request is enqueued as-is
    self.enqueue_yield_request(...);
}
``` [3](#0-2) 

On the node side, `verify_finality_level` maps `Latest` directly to `FinalityTag::Latest` and queries `eth_getBlockByNumber("latest")`. Any block number ≥ the receipt's block number passes:

```rust
let finality_tag = match finality {
    EthereumFinality::Finalized => FinalityTag::Finalized,
    EthereumFinality::Safe      => FinalityTag::Safe,
    EthereumFinality::Latest    => FinalityTag::Latest,   // ← accepted
};
// ...
if head.number < receipt_block_number {
    return Err(ForeignChainInspectionError::NotFinalized);
}
Ok(())
``` [4](#0-3) 

The signed payload encodes the full request — including the `finality` field — so the MPC signature is over `(request_with_Latest_finality, observed_values)`. A downstream NEAR contract that does not explicitly reject `Latest`-finality attestations will accept this signature as proof of a finalized event.

---

### Impact Explanation

The primary use case for `verify_foreign_transaction` is the Omnibridge inbound flow: a user locks funds on a foreign EVM chain and the MPC attestation authorizes minting on NEAR. If the attestation is produced against a `Latest`-finality block:

1. The attacker's deposit transaction appears in the latest block.
2. MPC nodes sign the attestation immediately.
3. The attacker submits the attestation to the NEAR bridge contract and receives minted tokens.
4. The attacker (or a colluding miner/validator on a PoS chain with short finality windows, or naturally on a chain with frequent shallow reorgs) causes or waits for a reorg that removes the deposit transaction.
5. The attacker retains both the minted NEAR-side tokens and the original EVM-side funds.

This is a **forged foreign-chain verification / light-client-style verification bypass enabling double-spend conditions** — matching the High impact tier.

---

### Likelihood Explanation

- The entry point is fully unprivileged: any NEAR account can call `verify_foreign_transaction` with `finality: Latest`.
- Shallow reorgs (1–2 blocks) occur naturally on Polygon, BNB Chain, and other EVM chains supported by the system. An attacker does not need to control mining/validation power; they only need to submit the request before the transaction's block is finalized and hope for (or induce) a reorg.
- The `EvmFinality::Latest` variant is exposed in the public SDK (`near-mpc-sdk`) and documented as a valid option, so callers are expected to use it.
- No on-chain or node-side guard rejects `Latest` finality for bridge-critical flows.

---

### Recommendation

Enforce a minimum finality level for `verify_foreign_transaction` requests. Two complementary approaches:

1. **Contract-level enforcement**: In `verify_foreign_transaction`, reject any `EvmRpcRequest` whose `finality` is `Latest` (and optionally `Safe` for chains where `Safe` is not cryptographically final). Panic with `InvalidParameters::InsufficientFinality`.

2. **Per-chain policy**: Store a per-chain minimum finality level in the on-chain `ForeignChainRpcWhitelist` (alongside the provider list and RPC quorum). The contract validates the request's finality against this policy before enqueuing.

The node-side `verify_finality_level` should be treated as a defense-in-depth check only; the authoritative gate must be in the contract, since nodes are not trusted individually.

---

### Proof of Concept

```rust
// Attacker submits with Latest finality immediately after broadcasting the EVM tx
let request = VerifyForeignTransactionRequestArgs {
    domain_id: foreign_tx_domain_id,
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Polygon(EvmRpcRequest {
        tx_id: EvmTxId(attacker_deposit_tx_hash),
        extractors: vec![EvmExtractor::BlockHash],
        finality: EvmFinality::Latest,   // ← weakest finality, no contract rejection
    }),
};
// Contract accepts; MPC nodes query eth_getBlockByNumber("latest"),
// find head.number >= receipt.block_number, sign the attestation.
// Attacker redeems on NEAR, then reorg removes the deposit on Polygon.
```

The contract's `verify_foreign_transaction` enqueues the request without inspecting `finality`: [5](#0-4) 

The node inspector accepts `Latest` and passes the check as long as the latest block is at or past the receipt block: [4](#0-3)

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
