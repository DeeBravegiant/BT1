### Title
`respond_verify_foreign_tx` does not verify that `response.payload_hash` corresponds to the submitted `request` — (File: crates/contract/src/lib.rs)

### Summary

The `respond_verify_foreign_tx` function in the MPC contract verifies only that the provided signature is cryptographically valid over `response.payload_hash`, but never checks that `response.payload_hash` is the canonical hash of the `request` being resolved. A single malicious attested participant (below threshold) can reuse a legitimately-produced MPC signature from one foreign-chain verification session to resolve a completely different pending request, delivering a forged `payload_hash` to the caller.

### Finding Description

`respond_verify_foreign_tx` performs the following check:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The contract verifies that `signature` is a valid ECDSA signature over `response.payload_hash` under the root public key. It does **not** verify that `response.payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload::V1({ request: request.request, values: <actual extracted values> })))`. The `request` field — which encodes the specific foreign-chain transaction, chain, extractors, and finality requirements — is used only as a map key to locate and drain the pending yield queue:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The `VerifyForeignTransactionRequest` struct contains `request`, `domain_id`, and `payload_version` — but the contract never reconstructs the expected `ForeignTxSignPayload` from these fields and compares its hash to `response.payload_hash`. [3](#0-2) 

The SDK-level verifier (`ForeignChainSignatureVerifier::verify_signature`) does perform this check — it reconstructs the expected hash from `(request, expected_extracted_values)` and compares it to `response.payload_hash` — but this check lives entirely off-chain in the SDK and is not enforced by the contract:

```rust
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [4](#0-3) 

The contract also uses the **root** public key (no tweak applied) for signature verification, confirmed by the test comment:

```rust
// simulate signature with the root key (no tweak for foreign tx)
``` [5](#0-4) 

This means any valid MPC signature over any hash — regardless of which request produced it — passes the contract's check, as long as the `request` key matches a pending entry.

### Impact Explanation

A single malicious attested participant (below signing threshold) can:

1. Participate honestly in the threshold signing session for request `R1` (e.g., Bitcoin `TX1`), obtaining a valid signature `S1` over `H1 = SHA-256(borsh(ForeignTxSignPayload::V1({ request: R1, values: V1 })))`.
2. Observe a separate pending request `R2` (e.g., Bitcoin `TX2`) submitted by a bridge contract or user.
3. Call `respond_verify_foreign_tx(request=R2, response={ payload_hash: H1, signature: S1 })`.
4. The contract accepts: `S1` is a valid signature over `H1` under the root key, and `R2` exists in the pending map.
5. The caller of `R2` receives `VerifyForeignTransactionResponse { payload_hash: H1, signature: S1 }` — a hash encoding the extracted values of `TX1`, not `TX2`.

A bridge or application contract that trusts the MPC contract's acceptance of the response (i.e., does not independently re-verify the hash against its own expected extracted values) will act on forged foreign-chain data. This enables invalid bridge execution or double-spend conditions — for example, releasing funds based on a block hash or log value extracted from a different transaction than the one the user submitted.

### Likelihood Explanation

- Requires one malicious attested participant (Byzantine node strictly below threshold). This is an explicitly in-scope attacker profile.
- No key forgery or threshold collusion is needed: the attacker reuses a legitimately-produced threshold signature from any prior signing session.
- Any attested participant can call `respond_verify_foreign_tx` for any pending request; there is no per-request ownership check.
- The window of opportunity exists whenever two different foreign-tx verification requests are pending concurrently, which is a normal production condition.
- Callers that rely on the contract's acceptance as the sole validity signal (without running the SDK's `ForeignChainSignatureVerifier::verify_signature`) are fully exploitable.

### Recommendation

Inside `respond_verify_foreign_tx`, reconstruct the expected `ForeignTxSignPayload` from the `request` fields and verify that `response.payload_hash` matches its canonical hash before accepting the response. Concretely, the contract should enforce:

```rust
// Reconstruct the minimum-bound hash from the request alone (without extracted values)
// OR require the responder to also submit the extracted values and verify them.
let expected_hash = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(), // add this field to the response
}).compute_msg_hash()?;
assert_eq!(expected_hash, response.payload_hash, "payload_hash does not match request");
```

Alternatively, include the extracted values in the response DTO and verify them on-chain, mirroring how `respond` derives and checks the expected public key from `request.tweak` before accepting a signature. [6](#0-5) 

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(R1 = { tx_id: TX1, chain: Bitcoin, extractors: [BlockHash] })
   → pending_verify_foreign_tx_requests[R1] = [yield_A]

2. MPC threshold protocol runs for R1:
   - Nodes query Bitcoin, extract block_hash = BH1
   - H1 = SHA-256(borsh(ForeignTxSignPayload::V1({ request: R1, values: [BH1] })))
   - Threshold signature S1 produced over H1

3. Bob submits verify_foreign_transaction(R2 = { tx_id: TX2, chain: Bitcoin, extractors: [BlockHash] })
   → pending_verify_foreign_tx_requests[R2] = [yield_B]

4. Malicious node (has S1 from step 2) calls:
   respond_verify_foreign_tx(
     request = R2,
     response = { payload_hash: H1, signature: S1 }
   )

5. Contract checks:
   - R2 exists in pending map ✓
   - verify_ecdsa_signature(S1, H1, root_pk) == Ok ✓
   - (no check that H1 encodes R2's actual extracted values) ✗

6. yield_B is resumed with { payload_hash: H1, signature: S1 }
   Bob's contract receives a response asserting TX2's block hash = BH1 (TX1's block hash).
   If Bob's contract does not re-verify the hash, it acts on forged data.
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/contract/src/lib.rs (L596-608)
```rust
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
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

**File:** crates/contract/src/lib.rs (L3694-3694)
```rust
        // simulate signature with the root key (no tweak for foreign tx)
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L147-150)
```rust
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
}
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-63)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```
