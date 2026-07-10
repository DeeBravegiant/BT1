### Title
Unbounded (Including Zero-Length) `extractors` Array in `verify_foreign_transaction` Allows MPC to Sign Foreign-Chain Payloads With No Actual Verification Data — (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

The `verify_foreign_transaction` contract endpoint accepts `ForeignChainRpcRequest` variants whose `extractors` field is a plain `Vec` with no minimum or maximum length enforced at the contract level. The design document explicitly states *"The request includes a bounded number of extractors"*, but no such bound is checked on-chain. An unprivileged caller can submit a request with zero extractors, causing MPC nodes to sign a payload `(request, [])` that contains no actual foreign-chain observations, bypassing the intended verification guarantee and enabling forged foreign-chain attestation.

---

### Finding Description

Every chain-specific request type carries an `extractors` field typed as a plain `Vec`:

```rust
pub struct EvmRpcRequest {
    pub tx_id: EvmTxId,
    pub extractors: Vec<EvmExtractor>,   // ← plain Vec, no lower bound
    pub finality: EvmFinality,
}

pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,
    pub extractors: Vec<BitcoinExtractor>, // ← plain Vec, no lower bound
}
``` [1](#0-0) 

The contract's `verify_foreign_transaction` method performs no length check on this field before enqueuing the request:

```rust
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    // ...checks chain support and domain purpose, but never checks extractors.len()
    let request = args_into_verify_foreign_tx_request(request);
    self.enqueue_yield_request(..., move |this, id| this.add_verify_foreign_tx_request(request, id));
}
``` [2](#0-1) 

On the node side, `execute_foreign_chain_request` iterates over the extractors list without any minimum-length guard:

```rust
let extractors: Vec<BitcoinExtractor> = request
    .extractors
    .iter()
    .cloned()
    .map(TryInto::try_into)
    .collect::<Result<_, _>>()?;
let extracted_values = inspector
    .extract(transaction_id, block_confirmations, extractors)
    ...
    .await??;
``` [3](#0-2) 

When `extractors` is empty, `inspector.extract(...)` returns `vec![]`. The node then constructs and signs:

```rust
ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: <original request with empty extractors>,
    values: vec![],   // ← no foreign-chain data whatsoever
})
``` [4](#0-3) 

The design document explicitly promises this invariant will be enforced:

> *"This design intentionally keeps responses small and on-chain-friendly by enforcing: … The request includes a **bounded** number of extractors."* [5](#0-4) 

The codebase even has a `BoundedVec` / `NonEmptyBoundedVec` infrastructure available for exactly this purpose, but it is not applied to the `extractors` field. [6](#0-5) 

---

### Impact Explanation

The MPC network's `verify_foreign_transaction` feature exists so that NEAR bridge contracts can react to foreign-chain events *without a trusted relayer*. The security guarantee is that the MPC threshold signature over `(request, observed_values)` cryptographically attests that the nodes independently queried the foreign chain and extracted specific data.

With zero extractors, the MPC signs `(request, [])` — a payload that contains no foreign-chain observations at all. A downstream bridge contract that:
1. Checks that the MPC signature is valid (it is — it is a genuine threshold signature), and
2. Checks that the `tx_id` in the signed payload matches the claimed deposit transaction,

but does **not** separately verify that the `values` array contains the expected extracted data (e.g., a block hash confirming finality), will accept this as proof that the foreign transaction was verified. The attacker can then claim a bridge deposit without ever making one on the foreign chain, causing direct fund theft from the bridge contract.

This maps to the allowed impact: **High — forged foreign-chain verification that causes invalid bridge execution.**

---

### Likelihood Explanation

The attack requires only:
- A 1 yoctoNEAR deposit (the minimum sign-request deposit).
- Submitting a `verify_foreign_transaction` call with `extractors: []` for any supported chain.

No privileged access, no collusion, and no threshold-level compromise is needed. Any unprivileged NEAR account can trigger this. The only downstream precondition is that a bridge contract does not independently validate the `values` array — a realistic omission given that the MPC contract is supposed to enforce the invariant itself.

---

### Recommendation

Enforce a non-empty, bounded extractors list at the contract boundary. The codebase already provides `NonEmptyBoundedVec` for this purpose. Replace the plain `Vec<EvmExtractor>` (and equivalent fields on all chain-specific request types) with a type that enforces `len >= 1` and `len <= MAX_EXTRACTORS`:

```rust
// In EvmRpcRequest, BitcoinRpcRequest, SolanaRpcRequest, etc.:
pub extractors: NonEmptyBoundedVec<EvmExtractor, 1, MAX_EXTRACTORS>,
```

Alternatively, add an explicit guard in `verify_foreign_transaction` before enqueuing:

```rust
if request.request.extractors_len() == 0 {
    env::panic_str("extractors must be non-empty");
}
```

The check must live in the contract (not only in the node) so that the invariant is enforced before the request is stored on-chain.

---

### Proof of Concept

1. Attacker calls `verify_foreign_transaction` with a `BitcoinRpcRequest` for a tx_id they do not control, with `extractors: []` and a valid `domain_id`.
2. The contract accepts the request (no length check) and enqueues it.
3. MPC nodes pick up the request, call `bitcoin_inspector.extract(tx_id, confirmations, [])`, receive `[]`, and sign `ForeignTxSignPayload::V1 { request, values: [] }`.
4. The attacker receives a valid MPC threshold signature over `(request, [])`.
5. The attacker submits this signature to a bridge contract that checks only signature validity and tx_id, not the content of `values`.
6. The bridge contract accepts the signature as proof that the foreign transaction was verified and releases funds to the attacker, even though no foreign-chain data was ever extracted or attested.

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L185-202)
```rust
impl ForeignChainRpcRequest {
    pub fn chain(&self) -> ForeignChain {
        match self {
            Self::Abstract(_) => ForeignChain::Abstract,
            Self::Ethereum(_) => ForeignChain::Ethereum,
            Self::Solana(_) => ForeignChain::Solana,
            Self::Bitcoin(_) => ForeignChain::Bitcoin,
            Self::Starknet(_) => ForeignChain::Starknet,
            Self::Bnb(_) => ForeignChain::Bnb,
            Self::Base(_) => ForeignChain::Base,
            Self::Arbitrum(_) => ForeignChain::Arbitrum,
            Self::Polygon(_) => ForeignChain::Polygon,
            Self::HyperEvm(_) => ForeignChain::HyperEvm,
            Self::Ton(_) => ForeignChain::Ton,
            Self::Aptos(_) => ForeignChain::Aptos,
        }
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1608-1632)
```rust
#[cfg(test)]
#[expect(non_snake_case)]
mod tests {
    use super::*;
    use rstest::rstest;

    #[test]
    fn foreign_tx_sign_payload_v1_ethereum__should_have_consistent_hash() {
        // Given
        let payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: ForeignChainRpcRequest::Ethereum(EvmRpcRequest {
                tx_id: EvmTxId([0xab; 32]),
                extractors: vec![EvmExtractor::BlockHash],
                finality: EvmFinality::Finalized,
            }),
            values: vec![ExtractedValue::EvmExtractedValue(
                EvmExtractedValue::BlockHash(Hash256([0xef; 32])),
            )],
        });

        // When
        let hash = payload.compute_msg_hash().unwrap();

        // Then
        insta::assert_json_snapshot!(hex::encode(hash.0));
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L139-150)
```rust
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

**File:** docs/foreign-chain-transactions.md (L26-30)
```markdown
This design intentionally keeps responses small and on-chain-friendly by enforcing:

* Each extractor returns **exactly one** typed value.
* The request includes a bounded number of extractors.
* Extracted values have strict size limits (e.g., bytes length caps).
```

**File:** crates/near-mpc-bounded-collections/src/bounded_vec.rs (L445-453)
```rust
/// A non-empty Vec with no effective upper-bound on its length
pub type NonEmptyVec<T> = BoundedVec<T, 1, { usize::MAX }, witnesses::NonEmpty<1, { usize::MAX }>>;

/// Possibly empty Vec with upper-bound on its length
pub type EmptyBoundedVec<T, const U: usize> = BoundedVec<T, 0, U, witnesses::PossiblyEmpty<U>>;

/// Non-empty Vec with bounded length
pub type NonEmptyBoundedVec<T, const L: usize, const U: usize> =
    BoundedVec<T, L, U, witnesses::NonEmpty<L, U>>;
```
