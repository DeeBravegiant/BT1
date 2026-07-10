### Title
Byzantine Participant Can Replay a Valid Foreign-Tx Signature Against a Different Pending Request, Delivering a Forged Verification - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` verifies only that the submitted ECDSA signature is valid over `response.payload_hash` and that the supplied `request` key exists in the pending queue. It never verifies that `payload_hash` was actually derived from `SHA-256(borsh(ForeignTxSignPayload { request, values }))` for the specific `request` being resolved. Any attested participant can therefore replay a legitimately-produced signature from one pending request against a different pending request, causing the contract to deliver a forged verification to the second caller.

### Finding Description

`ForeignTxSignPayloadV1` binds a `ForeignChainRpcRequest` together with the extracted `values` the MPC nodes observed on the foreign chain:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
``` [1](#0-0) 

The 32-byte `msg_hash` that the MPC network signs is `SHA-256(borsh(ForeignTxSignPayload))`. The contract's `respond_verify_foreign_tx` handler performs two checks:

1. The ECDSA signature in `response` is valid over `response.payload_hash` against the domain's root public key.
2. The `request` argument exists as a key in `pending_verify_foreign_tx_requests`. [2](#0-1) 

There is **no check** that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload { request: <the request argument>, values: <anything> }))`. The contract stores the `request` in the response key but never uses it to validate the hash — an exact structural parallel to the ULTI contract storing `inputTokenDecimals` and then ignoring it in every calculation.

### Impact Explanation

An attested participant (Byzantine node, strictly below the signing threshold) can:

1. Observe a legitimately completed `respond_verify_foreign_tx` call for request **R1** on the public NEAR ledger and extract `{payload_hash: H1, signature: sig_H1}`.
2. While a different request **R2** is still pending, call `respond_verify_foreign_tx(R2, {payload_hash: H1, signature: sig_H1})`.
3. The contract accepts: `sig_H1` is a valid ECDSA signature over `H1` under the root key ✓, and `R2` exists in the pending queue ✓.
4. The contract resolves R2's yield and returns `{payload_hash: H1, signature: sig_H1}` to the user who submitted R2.

The user who submitted R2 receives a threshold signature that commits to **R1's foreign-chain state** (a different transaction, possibly a different chain entirely), not R2's. Any downstream smart contract that trusts the returned `payload_hash` without independently recomputing it from the expected values will accept a forged verification, enabling invalid bridge execution or double-spend conditions.

**Impact: High** — matches "forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."

### Likelihood Explanation

- The attacker need only be an **attested participant** (no threshold collusion required).
- NEAR transactions are public; extracting `{payload_hash, signature}` from a completed `respond_verify_foreign_tx` call requires only chain indexing.
- Two concurrent pending requests is a normal production condition for any active bridge.
- The attacker does not need to have been the signing leader for R1.

**Likelihood: Low-Medium** — requires a Byzantine attested participant and two overlapping pending requests, both realistic in production.

### Recommendation

Inside `respond_verify_foreign_tx`, reconstruct the expected payload hash from the `request` argument and compare it against `response.payload_hash` before accepting the response. Because the contract does not receive the extracted `values`, the simplest fix is to include the `values` in the response DTO and have the contract verify:

```
response.payload_hash == SHA-256(borsh(ForeignTxSignPayload::V1 { request, values }))
```

Alternatively, bind the `payload_hash` to the specific pending-request key at submission time (store the expected hash alongside the yield index) so that a mismatched hash is rejected even without knowing `values`.

### Proof of Concept

**Setup**: Two requests are pending simultaneously.
- R1 = `ForeignChainRpcRequest::Bitcoin { tx_id: [0xaa;32], … }`
- R2 = `ForeignChainRpcRequest::Ethereum { tx_id: [0xbb;32], … }`

**Step 1** — MPC network legitimately processes R1. The leader calls:
```
respond_verify_foreign_tx(R1, { payload_hash: H1, signature: sig_H1 })
```
This transaction is visible on the NEAR ledger.

**Step 2** — Byzantine attested participant extracts `{H1, sig_H1}` from the ledger.

**Step 3** — Byzantine participant calls:
```
respond_verify_foreign_tx(R2, { payload_hash: H1, signature: sig_H1 })
```

**Step 4** — Contract execution path in `respond_verify_foreign_tx`:

```rust
// sig_H1 is valid over H1 under the root key → passes
near_mpc_signature_verifier::verify_ecdsa_signature(sig_H1, &H1, &secp_pk).is_ok()
// R2 exists in pending_verify_foreign_tx_requests → passes
pending_requests::resolve_yields_for(&mut self.pending_verify_foreign_tx_requests, &R2, …)
``` [3](#0-2) 

**Result**: The user who submitted R2 (an Ethereum transaction) receives `{payload_hash: H1, signature: sig_H1}` — a valid MPC signature over Bitcoin transaction R1's data. Any bridge contract that does not independently recompute the expected hash from R2's values will accept this as proof that R2 was verified.

### Citations

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

**File:** crates/contract/src/lib.rs (L718-753)
```rust
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
```
