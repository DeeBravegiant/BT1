### Title
`respond_verify_foreign_tx` Does Not Bind `response.payload_hash` to the Pending `request`, Enabling a Single Byzantine MPC Participant to Deliver a Forged Foreign-Chain Attestation — (File: `crates/contract/src/lib.rs`)

---

### Summary

The on-chain MPC contract's `respond_verify_foreign_tx` method verifies only that the submitted ECDSA signature is mathematically valid over `response.payload_hash`. It never checks that `response.payload_hash` was actually derived from the pending `request` stored in `pending_verify_foreign_tx_requests`. A single attested-but-Byzantine MPC participant (the signing-round leader) can therefore recycle a legitimately-produced threshold signature from **any other completed request** and submit it as the response to a completely different pending request. The contract accepts it, the yield is resolved, and the bridge contract receives an attestation whose `payload_hash` encodes a foreign-chain transaction it never asked about.

---

### Finding Description

In `crates/contract/src/lib.rs` the `respond_verify_foreign_tx` function performs the following checks:

```rust
// line 726
let payload_hash: [u8; 32] = response.payload_hash.0;

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The `payload_hash` is taken verbatim from the caller-supplied `response`; the contract never recomputes it from the pending `request`. After the signature check passes, the full `response` (including the attacker-chosen `payload_hash`) is serialised and delivered to every waiting yield:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

The correct binding is defined in the node-side code. Both the leader and every follower independently compute:

```
payload_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))
``` [3](#0-2) 

and the threshold protocol signs exactly that hash. The contract, however, never re-derives this hash from the stored `request`; it simply trusts whatever hash the responding node supplies.

The SDK ships a client-side verifier that **does** enforce the binding:

```rust
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { … });
}
``` [4](#0-3) 

but this check lives entirely off-chain in the SDK and is not enforced by the contract.

---

### Impact Explanation

**Forged foreign-chain verification / invalid bridge execution (High)**

A Byzantine leader node that has participated in (and therefore observed the final output of) a legitimate signing round for **Request B** (e.g., Bitcoin tx_id Y, 1 confirmation, `BlockHash` extractor) can immediately call `respond_verify_foreign_tx` with:

| Field | Value |
|---|---|
| `request` | the pending **Request A** (Bitcoin tx_id X, 6 confirmations) |
| `response.payload_hash` | `hash(request_B, [block_hash_Z])` — from Request B |
| `response.signature` | the valid threshold signature over that hash |

The contract accepts the call (signature is valid over the supplied hash; Request A exists in `pending_verify_foreign_tx_requests`). The bridge contract waiting on Request A receives a `VerifyForeignTransactionResponse` whose `payload_hash` encodes **tx_id Y with 1 confirmation**, not tx_id X with 6 confirmations.

Because the bridge contract cannot independently recompute the expected `payload_hash` without knowing the extracted values (which are determined by the MPC nodes, not the bridge), it cannot detect the substitution. A bridge contract that releases funds upon receiving a valid MPC signature would therefore release funds based on a transaction it never verified — enabling theft or double-spend.

---

### Likelihood Explanation

The attacker is a

### Citations

**File:** crates/contract/src/lib.rs (L718-747)
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
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-48)
```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
        foreign_tx_payload.compute_msg_hash()?.into();
    let payload_bytes: BoundedVec<u8, ECDSA_PAYLOAD_SIZE_BYTES, ECDSA_PAYLOAD_SIZE_BYTES> =
        payload_hash.into();

    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
}
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L53-64)
```rust
        let expected_payload_hash = expected_payload
            .compute_msg_hash()
            .map_err(|_| VerifyForeignChainError::FailedToComputeMsgHash)?;

        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```
