### Title
Single Byzantine Participant Can Deliver Fabricated CKD Response for `AppPublicKey` Variant, Bypassing Threshold Requirement - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_ckd` enforces that the caller is an attested participant but performs **no cryptographic output verification** when the request uses the `AppPublicKey` (legacy) variant. A single Byzantine attested participant can call `respond_ckd` with an arbitrary fabricated `CKDResponse` — crafted so that the user's decrypted derived key is a scalar known to the attacker — and the contract will accept and deliver it. This bypasses the threshold requirement for confidential key derivation entirely.

### Finding Description

**Root cause — missing output check for `AppPublicKey` variant:**

In `respond_ckd` (`crates/contract/src/lib.rs`, lines 675–682):

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, the contract verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)` via `ckd_output_check` (`crates/contract/src/primitives/ckd.rs`, lines 80–101). For `AppPublicKey`, **no such check exists**. Any `big_y` and `big_c` values are accepted.

**Attacker-controlled entry path:**

1. User submits `request_app_private_key` with `AppPublicKey(app_pk1)`. The pending `CKDRequest` — including `app_pk1` — is stored on-chain and publicly readable.
2. Byzantine attested participant reads `app_pk1` from the pending request.
3. Attacker chooses arbitrary scalar `t` (their target private key) and scalar `r`, then computes:
   - `big_y = G1 * r`
   - `big_c = G1 * t + app_pk1 * r`
4. Attacker calls `respond_ckd(request, CKDResponse { big_y, big_c })`. The `assert_caller_is_attested_participant_and_protocol_active()` check passes (attacker is an attested participant). No output check is performed for `AppPublicKey`.
5. `resolve_yields_for` drains the queue and delivers the fabricated response to the user.
6. User decrypts: `big_s = big_c − app_sk * big_y = G1*t + app_pk1*r − app_sk*(G1*r) = G1*t`.
7. The user's derived key is `G1 * t`. The attacker knows `t` — the discrete log — and therefore controls the user's derived private key.

**Why the "attested participant" check is analogous to Perennial's `closable = 0`:**

In Perennial, `closable = 0` was supposed to prevent position increases during liquidation, but the check passed while the position was still increased. Here, `assert_caller_is_attested_participant` is supposed to ensure only legitimate threshold-computed responses are delivered, but it passes while a single Byzantine participant delivers a fabricated response — bypassing the threshold requirement entirely.

The first `respond_ckd` call wins (drains the queue via `resolve_yields_for`). Honest nodes must complete a multi-round threshold protocol before responding; the attacker can respond immediately upon seeing the request on-chain, winning the race.

### Impact Explanation

A single Byzantine attested participant can deliver a fabricated CKD response for any pending `AppPublicKey` request. By crafting `big_c = G1*t + app_pk1*r` and `big_y = G1*r` for attacker-chosen `t`, the attacker makes the user's derived private key equal to `t` — a value the attacker knows. The attacker can then sign arbitrary transactions on foreign chains using the user's derived key, stealing any funds the user controls with that key. This constitutes unauthorized confidential key derivation output without the required threshold participant authorization.

### Likelihood Explanation

The attacker must be an attested participant (Byzantine participant below the signing threshold — an explicitly allowed attacker profile). The attack requires no cryptographic break: only reading a public on-chain value (`app_pk1`) and submitting a crafted transaction. The race against honest nodes is favorable to the attacker because honest nodes must complete a multi-round MPC protocol before responding, while the attacker responds immediately. The `AppPublicKey` variant is still actively supported in the contract.

### Recommendation

Add cryptographic output verification for the `AppPublicKey` variant in `respond_ckd`. One approach: require the caller to prove that `big_s = big_c − app_sk * big_y` lies on the correct coset (i.e., verify `e(big_c, G2) = e(big_y, app_pk1_g2) · e(hash_point, public_key_g2)` using a G2 representation of `app_pk1`). Alternatively, deprecate the `AppPublicKey` variant entirely and require all new requests to use `AppPublicKeyPV`, which already has a pairing-based output check.

### Proof of Concept

```
1. Contract is Running with participants [P0, P1, P2], threshold=2.
   P0 is Byzantine.

2. User Alice submits:
   request_app_private_key({
     derivation_path: "my/path",
     app_public_key: AppPublicKey(app_pk1),   // app_pk1 = G1 * app_sk
     domain_id: 0
   })
   → CKDRequest stored on-chain with app_pk1 visible.

3. P0 (Byzantine) reads app_pk1 from contract state.
   P0 chooses t = 42 (attacker's target scalar), r = 7.
   P0 computes:
     big_y = G1 * 7
     big_c = G1 * 42 + app_pk1 * 7

4. P0 calls respond_ckd(ckd_request, CKDResponse { big_y, big_c })
   before honest nodes complete the threshold protocol.
   → assert_caller_is_attested_participant passes (P0 is attested).
   → AppPublicKey branch: no output check.
   → resolve_yields_for delivers response to Alice.

5. Alice decrypts:
   big_s = big_c − app_sk * big_y
         = G1*42 + app_pk1*7 − app_sk*(G1*7)
         = G1*42 + G1*(app_sk*7) − G1*(app_sk*7)
         = G1*42

6. Alice's derived private key = 42. P0 knows 42.
   P0 can now sign any foreign-chain transaction on Alice's behalf.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L653-689)
```rust
    #[handle_result]
    pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();

        let PublicKeyExtended::Bls12381 {
            public_key: dtos::PublicKey::Bls12381(public_key),
        } = self.public_key_extended(request.domain_id)?
        else {
            env::panic_str("Domain is not compatible with CKD (expected Bls12381 curve)");
        };

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L80-101)
```rust
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
```

**File:** crates/contract/src/pending_requests.rs (L62-88)
```rust
/// Resume every yield queued for `request` with `response_bytes`, draining the
/// fan-out map in one pass. Returns `Err(RequestNotFound)` if the map held no entry.
///
/// Resuming a yield that has already timed out is a no-op at the SDK level.
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```
