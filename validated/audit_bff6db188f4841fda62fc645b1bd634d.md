### Title
Cross-Request Replay of `ForeignTxSignPayload` Signatures Enables Stale Foreign-Chain Attestation Delivery — (File: `crates/contract/src/lib.rs`, `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the submitted `response.payload_hash` carries a valid MPC root-key signature. It does **not** verify that `payload_hash` is the canonical hash of the actual foreign-chain data observed for the pending `request`. Because `ForeignTxSignPayload::compute_msg_hash()` commits to no nonce, no timestamp, and no contract-instance identifier, a single malicious attested participant can replay a previously produced `(payload_hash, signature)` pair against any future pending request that shares the same `VerifyForeignTransactionRequest` key, delivering stale or reorganized foreign-chain data to the bridge caller.

---

### Finding Description

`ForeignTxSignPayload::compute_msg_hash()` is defined as:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload { request, values }))
``` [1](#0-0) 

The hash commits only to the `ForeignChainRpcRequest` (tx_id, extractors, finality) and the extracted `values`. It includes no nonce, no per-request unique identifier, no NEAR contract account ID, and no timestamp.

`respond_verify_foreign_tx` then performs the following check:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [2](#0-1) 

The contract verifies only that the caller-supplied `payload_hash` carries a valid signature under the MPC root key. It does **not** verify that `payload_hash` equals `SHA-256(borsh(ForeignTxSignPayload{request, values}))` for the specific `request` being resolved, nor does it check that the `values` are fresh. The `values` field is not present in the `respond_verify_foreign_tx` call at all.

After signature verification passes, the contract resolves all queued yields for the `request` key:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [3](#0-2) 

The `VerifyForeignTransactionRequest` key used for the pending-request map contains no nonce:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [4](#0-3) 

Because the key is deterministic and nonce-free, the same `request` key can appear in `pending_verify_foreign_tx_requests` multiple times across separate submissions (e.g., after a blockchain reorg, or when a bridge service re-queries the same tx).

---

### Impact Explanation

A single malicious attested participant who has observed a prior legitimate `respond_verify_foreign_tx` call can:

1. Save the old `(payload_hash_old, sig_old)` from a past response for foreign tx `X` (e.g., Bitcoin tx X confirmed in block B1).
2. Wait for a blockchain reorg: tx X is now in block B2 (different block hash), or the bridge service re-submits `verify_foreign_transaction(tx_id=X)` for any reason.
3. Call `respond_verify_foreign_tx(request_X, {payload_hash_old, sig_old})`.
4. The contract accepts it — the signature is valid (produced by the full MPC network previously) — and delivers the stale `payload_hash_old` (pointing to B1) to the bridge service.

The bridge service receives a `VerifyForeignTransactionResponse` attested by the MPC root key, but the attested data is stale. If the bridge service uses this attestation to authorize a cross-chain transfer (e.g., "tx X was finalized in block B1"), it may accept a transfer that was reorganized away, enabling a **double-spend**.

This matches the allowed impact: **Cross-chain replay / forged foreign-chain verification that causes invalid bridge execution or double-spend conditions (High)**.

---

### Likelihood Explanation

- Requires a single malicious **attested participant** — one who has passed TEE attestation and is part of the active participant set. This is "Byzantine participant strictly below the signing threshold."
- The attacker needs only to have observed one prior legitimate response for the target `request` key. Since responses are submitted on-chain, any participant can observe them.
- The attack is most impactful on chains with reorg risk (Bitcoin, Ethereum pre-finality) or when bridge services re-query the same tx.
- No threshold collusion is required; a single participant suffices.

---

### Recommendation

1. **Include a per-request unique identifier in `ForeignTxSignPayload`**: Bind the signature to the specific NEAR yield/request ID (e.g., `receipt_id` or a monotonic nonce stored in the contract) so that a signature produced for one request instance cannot be replayed for another.

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
    pub request_id: CryptoHash,  // receipt_id or unique yield ID
}
```

2. **Verify `payload_hash` against `request` in `respond_verify_foreign_tx`**: The contract should recompute the expected `payload_hash` from the `request` fields and reject responses where `response.payload_hash` does not match. This requires the `values` to be submitted alongside the response, or the contract to store the expected hash at request time.

3. **Include the NEAR contract account ID** in the hash to prevent cross-instance replay if the contract is ever redeployed at a different address.

---

### Proof of Concept

**Setup**: MPC network with one malicious attested participant `M`. Bitcoin tx `X` is confirmed in block `B1`.

1. Bridge service calls `verify_foreign_transaction({tx_id: X, extractors: [BlockHash], domain_id: D})`.
2. MPC nodes (including `M`) observe block hash `B1`, compute `payload_hash_B1 = SHA-256(borsh({request_X, BlockHash=B1}))`, and sign it.
3. `M` saves `(payload_hash_B1, sig_B1)`.
4. `respond_verify_foreign_tx(request_X, {payload_hash_B1, sig_B1})` is submitted. Contract verifies signature ✓, resolves yield, bridge service receives `B1`.
5. Bitcoin reorg occurs: tx `X` is now in block `B2`.
6. Bridge service re-submits `verify_foreign_transaction({tx_id: X, extractors: [BlockHash], domain_id: D})` — same `VerifyForeignTransactionRequest` key.
7. `M` immediately calls `respond_verify_foreign_tx(request_X, {payload_hash_B1, sig_B1})` before honest nodes respond.
8. Contract checks: `sig_B1` valid over `payload_hash_B1` under root key ✓. Resolves yield. Bridge service receives stale `B1` instead of `B2`.
9. Bridge service, trusting the MPC attestation, authorizes a transfer based on the reorganized block — double-spend enabled.

The root cause is confirmed at:
- `ForeignTxSignPayload::compute_msg_hash()` — no nonce/request-id binding [1](#0-0) 
- `respond_verify_foreign_tx` — no cross-check of `payload_hash` against `request` content [5](#0-4)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/contract/src/lib.rs (L514-557)
```rust
    /// Submit a verification + signing request for a foreign chain transaction.
    /// MPC nodes will verify the transaction on the foreign chain before signing.
    /// The signed payload is derived from the transaction ID (hash of tx_id).
    #[handle_result]
    #[payable]
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
