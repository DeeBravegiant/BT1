### Title
Missing CKD Response Integrity Verification for `AppPublicKey` Variant Allows Single Malicious Participant to Substitute Derived Key - (File: crates/contract/src/lib.rs)

### Summary

`respond_ckd` performs no cryptographic verification of the `CKDResponse` when the request uses the `AppPublicKey` (privately verifiable / legacy) variant. A single attested participant below the signing threshold can front-run honest nodes, deliver a fabricated response encoding a key they control, and cause the user to receive a compromised app-specific private key.

### Finding Description

The `respond_ckd` function in `crates/contract/src/lib.rs` contains an asymmetric verification branch: [1](#0-0) 

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← NO CHECK
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For the `AppPublicKeyPV` variant, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the network master key and the user's app identity: [2](#0-1) 

For the `AppPublicKey` variant, the contract performs **zero verification** of `big_c` and `big_y` before calling `resolve_yields_for`, which immediately removes the pending entry and resumes the user's yield with the attacker-supplied bytes: [3](#0-2) 

The `AppPublicKey` variant is the legacy, still-supported format. It is accepted by `request_app_private_key` and stored in `pending_ckd_requests`: [4](#0-3) 

The `CKDRequest` key stored on-chain includes the user's ephemeral `app_public_key` (`app_pk`, a BLS12-381 G1 point), which is publicly readable: [5](#0-4) 

`resolve_yields_for` uses `.unwrap_or_default()` so it only returns `RequestNotFound` if the entry is absent — the first caller wins and drains the queue: [6](#0-5) 

### Impact Explanation

The CKD protocol encrypts the derived key `big_s = H(network_pk, app_id) · msk` under the user's ephemeral public key `app_pk = G1 · app_secret`. The user decrypts via `big_c − big_y · app_secret`.

An attacker who reads `app_pk` from the pending request can construct a fully valid-looking response for any scalar `k` they choose:

- Set `big_y = G1 · r` for arbitrary `r`
- Set `big_c = G1 · k + app_pk · r`
- User decrypts: `big_c − big_y · app_secret = G1·k + G1·app_secret·r − G1·r·app_secret = G1·k`

The user receives `G1·k` as their app-specific key, and the attacker knows `k`. The attacker can then use `k` to sign transactions or access any resource protected by that derived key, enabling direct theft of funds from the user's app-specific wallet.

This is a **Critical** impact: confidential key derivation output substitution by a single participant below threshold, enabling theft of funds and unauthorized transaction execution.

### Likelihood Explanation

- The `AppPublicKey` variant is still actively supported and documented as the legacy format in the README.
- The pending request (including `app_pk`) is publicly visible on-chain immediately after `request_app_private_key` is called.
- The honest MPC nodes must complete a multi-round threshold protocol before calling `respond_ckd`. The attacker can submit their fabricated response immediately, front-running the honest nodes.
- Only one attested participant is required — no threshold collusion needed.
- The attacker constructs the fake response with simple scalar multiplication, requiring no knowledge of the network master secret.

### Recommendation

1. **Deprecate `AppPublicKey`** in favor of `AppPublicKeyPV` for all new `request_app_private_key` calls. The `AppPublicKeyPV` variant was introduced precisely to enable on-chain verifiability; the legacy variant cannot be verified on-chain without the G2 component `pk2`.
2. **Reject `AppPublicKey` requests in `respond_ckd`** with an explicit error, or require callers to upgrade to `AppPublicKeyPV`.
3. If backward compatibility must be preserved, document clearly that `AppPublicKey` CKD responses carry no on-chain integrity guarantee and that users must verify the decrypted key matches the expected derivation path out-of-band.

### Proof of Concept

1. Alice calls `request_app_private_key` with `app_public_key = AppPublicKey(app_pk)` where `app_pk = G1 · app_secret`. The request is stored in `pending_ckd_requests` and `app_pk` is publicly visible on-chain.

2. Mallory (a single attested participant) reads `app_pk` from the pending request. She picks arbitrary scalars `k` and `r`, computes:
   - `big_y = G1 · r`
   - `big_c = G1 · k + app_pk · r`

3. Mallory calls `respond_ckd(request, CKDResponse { big_c, big_y })` before the honest nodes finish their MPC round.

4. The contract's `AppPublicKey` branch executes the empty arm (`{}`), skipping all verification. `resolve_yields_for` removes the pending entry and resumes Alice's yield with Mallory's fabricated bytes.

5. Alice decrypts: `big_c − big_y · app_secret = G1·k`. She uses `G1·k` as her app-specific key.

6. Mallory, knowing `k`, can derive the same key and sign any transaction on Alice's behalf, stealing funds from Alice's app-specific wallet.

### Citations

**File:** crates/contract/src/lib.rs (L484-498)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }

        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
        );
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

**File:** crates/contract/src/primitives/ckd.rs (L8-30)
```rust
#[derive(Debug, Clone, Eq, Ord, PartialEq, PartialOrd)]
#[near(serializers=[borsh, json])]
pub struct CKDRequest {
    /// The app ephemeral public key
    pub app_public_key: dtos::CKDAppPublicKey,
    pub app_id: dtos::CkdAppId,
    pub domain_id: DomainId,
}

impl CKDRequest {
    pub fn new(
        app_public_key: dtos::CKDAppPublicKey,
        domain_id: DomainId,
        predecessor_id: &AccountId,
        derivation_path: &str,
    ) -> Self {
        let app_id = derive_app_id(predecessor_id, derivation_path);
        Self {
            app_public_key,
            app_id,
            domain_id,
        }
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L80-102)
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
}
```

**File:** crates/contract/src/pending_requests.rs (L74-87)
```rust
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
```
