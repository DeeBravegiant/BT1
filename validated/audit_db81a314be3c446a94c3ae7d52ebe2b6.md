### Title
Missing CKD Output Validation for `AppPublicKey` Variant in `respond_ckd` Allows Byzantine Coordinator to Inject Fraudulent Key Material - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` in the MPC signer contract applies a cryptographic output check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of a CKD request. The `AppPublicKey` (privately-verifiable / legacy) variant has an **empty match arm** — no check whatsoever. A single Byzantine coordinator node (an attested MPC participant, strictly below the signing threshold) can call `respond_ckd` with an arbitrary `(big_c, big_y)` pair for any pending `AppPublicKey` request. The contract accepts it unconditionally, resolves the user's yield with fraudulent key material, and removes the pending request from the queue.

---

### Finding Description

In `respond_ckd` (`crates/contract/src/lib.rs`, lines 675–682):

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← empty: no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, G2) = e(big_y, app_pk2) · e(H(pk, app_id), mpc_pk)`, which cryptographically binds the response to the MPC master secret and the user's ephemeral key. [2](#0-1) 

For `AppPublicKey`, **no equivalent check exists**. The contract proceeds directly to `pending_requests::resolve_yields_for`, delivering whatever `(big_c, big_y)` the caller supplied. [3](#0-2) 

The analog to the reported vulnerability is exact: just as `pre_deposit_check_regulated` aborted on `get_force_full_transfer() && from_region == US` without also checking whether the transfer was actually full, `respond_ckd` applies the output validity gate only for one variant of the request type and silently skips it for the other.

The CKD protocol specification explicitly states that the coordinator must verify the aggregated output before sending it on-chain:

> **Publicly verifiable variant:** Verifies that `es` is a valid encryption of a signature with respect to the MPC network public key `pk`. [4](#0-3) 

The off-chain coordinator protocol (`do_ckd_coordinator`) does run `aggregated_output_check` internally before returning, but only for the `AppPublicKeyPV` path (`protocol_pv.rs`). For `AppPublicKey`, the off-chain check is also absent at the contract layer, and the contract is the last line of defense against a Byzantine coordinator submitting a forged response. [5](#0-4) 

---

### Impact Explanation

A Byzantine coordinator node calls `respond_ckd` with a fabricated `CKDResponse { big_c, big_y }` for a pending `AppPublicKey` request. The contract:

1. Passes all access-control checks (the node is an attested participant).
2. Skips the output check (empty match arm).
3. Calls `resolve_yields_for`, which delivers the fraudulent `(big_c, big_y)` to the waiting user promise and removes the pending request entry.

The user receives key material that does not satisfy `e(H(pk, app_id), mpc_pk) = e(sig, G2)`. If the user's application does not independently verify the response (the contract README describes `AppPublicKey` as the "privately verifiable" variant, implying verification is the user's responsibility), the user may derive and use a key that is either:

- **Attacker-predictable**: if the attacker sets `big_y = G1_identity` and `big_c = t·G1` for a known scalar `t`, the decrypted secret is `t·G1`, fully known to the attacker. Any funds sent to an address derived from this key can be stolen.
- **Permanently inaccessible**: if the attacker supplies random garbage, the user's derived key is wrong and any funds sent to the resulting address are frozen.

In either case the pending request is consumed; the user cannot obtain a valid response for the same request without submitting a new one.

**Impact category**: Medium — request-lifecycle and contract execution-flow manipulation that breaks the production safety invariant (CKD responses must be cryptographically bound to the MPC master secret) without requiring network-level DoS or operator misconfiguration. Escalates to Critical if the attacker chooses `(big_c, big_y)` to make the derived key predictable, enabling direct theft of funds from addresses the user creates with the fraudulent key.

---

### Likelihood Explanation

- **Attacker model**: a single attested MPC participant acting as the signing-round coordinator — strictly below the threshold, consistent with the allowed scope.
- **Entry path**: `respond_ckd` is a public contract method; any attested participant can call it directly on NEAR.
- **No collusion required**: one node suffices; the first valid `respond_ckd` call wins and resolves the yield.
- **Detection difficulty**: the contract emits no on-chain event distinguishing a fraudulent from a legitimate response; the user's only recourse is client-side verification, which is not enforced.

Likelihood: **Medium** — requires a compromised or malicious MPC node, which is a realistic threat in a permissioned-but-adversarial MPC network.

---

### Recommendation

Apply an equivalent output validity check for the `AppPublicKey` variant. Because `AppPublicKey` does not carry a G2 component (`pk2`), the full `ckd_output_check` pairing equation cannot be applied directly. The minimum fix is to verify that `big_c` and `big_y` are valid, non-identity BLS12-381 G1 points (rejecting trivially forged responses), and to document that full binding to the MPC master secret requires the user to verify `e(H(pk, app_id), mpc_pk) = e(big_c - a·big_y, G2)` client-side.

A stronger fix is to require callers to use `AppPublicKeyPV` for all new requests (deprecating `AppPublicKey`) so that the on-chain pairing check can always be enforced, matching the security guarantee already provided for the publicly-verifiable variant.

---

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey(pk1)` and a valid deposit. A `CKDRequest` is enqueued. [6](#0-5) 

2. Byzantine coordinator node (attested participant) calls `respond_ckd(ckd_request, CKDResponse { big_c: G1_generator, big_y: G1_identity })` — values chosen so the attacker knows the resulting secret.

3. The contract executes the match:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}  // no check, falls through
    ...
}
``` [7](#0-6) 

4. `resolve_yields_for` is called with the fraudulent response bytes; the user's yield is resumed with `{ big_c: G1_generator, big_y: G1_identity }`. [3](#0-2) 

5. The pending request is removed. The user receives `big_c = G1_generator`, `big_y = G1_identity`. Decrypted secret = `G1_generator - a · G1_identity = G1_generator` — a point the attacker knows. Any key derived from it is under attacker control.

### Citations

**File:** crates/contract/src/lib.rs (L484-491)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L675-682)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/primitives/ckd.rs (L76-102)
```rust
/// Check that `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`.
///
/// Point validation is fully delegated to the host, as in
/// [`app_public_key_check`].
pub(crate) fn ckd_output_check(
    app_id: &dtos::CkdAppId,
    output: &CKDResponse,
    app_public_key: &dtos::CKDAppPublicKeyPV,
    public_key: &dtos::Bls12381G2PublicKey,
) -> bool {
    let big_c = env::bls12381_p1_decompress(&output.big_c);
    let big_y = env::bls12381_p1_decompress(&output.big_y);
    let pk2 = env::bls12381_p2_decompress(&app_public_key.pk2);
    let pk = env::bls12381_p2_decompress(public_key);
    let hash_point = hash_app_id_with_pk(public_key.as_slice(), app_id.as_ref());

    let pairing_input = [
        big_c.as_slice(),
        MINUS_G2_GENERATOR_UNCOMPRESSED.as_slice(),
        big_y.as_slice(),
        pk2.as_slice(),
        hash_point.as_slice(),
        pk.as_slice(),
    ]
    .concat();
    env::bls12381_pairing_check(&pairing_input)
}
```

**File:** crates/threshold-signatures/docs/confidential_key_derivation/confidential-key-derivation.md (L184-188)
```markdown
    > **Publicly verifiable variant:** Verifies that $es$ is a valid
    > encryption of a signature with respect to the MPC network public key
    > $`\texttt{pk}`$, i.e.
    > $`e(C, G_2) = e\bigl(H(\texttt{pk},\, \texttt{app\_id}),\; \texttt{pk}\bigr) \cdot e(Y, A_2)`$
    - sends $`\texttt{es}`$ to *app* on-chain
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L66-70)
```rust
    if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
        return Err(ProtocolError::AssertionFailed(
            "CKD output failed to verify".to_string(),
        ));
    }
```
