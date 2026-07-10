### Title
Byzantine Participant Can Corrupt CKD Output via Inconsistent Lagrange Coefficients — (`crates/threshold-signatures/src/confidential_key_derivation/protocol.rs`)

### Summary

The non-PV `ckd` protocol in `protocol.rs` has no output integrity check. A Byzantine participant can call `ckd()` with a different `participants` list than the rest of the group, compute their share using a wrong Lagrange coefficient, and send it to the coordinator. The coordinator blindly sums all received `CKDOutput` values, producing an aggregated result that is cryptographically invalid and non-reproducible.

---

### Finding Description

In `compute_signature_share`, each participant computes their Lagrange coefficient from the `participants` list they were initialized with: [1](#0-0) 

The `participants` list is supplied by the caller of `ckd()` — there is no protocol-level mechanism that forces all participants to use the same list. A Byzantine participant can call `ckd()` with a truncated or modified participant list (e.g., omitting one honest participant), causing their `lambda_i` to differ from what the honest participants computed.

The coordinator in `do_ckd_coordinator` then aggregates all received shares by simple addition: [2](#0-1) 

There is **no verification** of the aggregated output. The coordinator returns the result directly: [3](#0-2) 

Compare this to the public-verifiability variant `protocol_pv.rs`, which **does** perform a pairing check after aggregation: [4](#0-3) 

The `aggregated_output_check` verifies `e(C, g2) = e(Y, app_pk2) · e(H(pk||app_id), pk)`, which would catch any inconsistency in the Lagrange coefficients. This guard is entirely absent from `protocol.rs`.

The `recv_from_others` helper only checks that messages originate from known participants — it does not inspect share content: [5](#0-4) 

---

### Impact Explanation

When a Byzantine participant uses a wrong participant set, the aggregated `C = Σ(λ_i · C_i)` has inconsistent `λ_i` values. The result does not equal `msk · H(pk || app_id)`, so `unmask(app_sk)` returns a wrong point: [6](#0-5) 

The derived key is cryptographically invalid and non-reproducible, breaking the core CKD invariant that the same `app_id` always yields the same confidential key. Any downstream use of this key (encryption, signing) silently fails.

---

### Likelihood Explanation

The attacker must be one of the MPC participants (a Byzantine operator). They need only call `ckd()` with a modified participant slice — a single-line change in their local node. No threshold collusion is required; a single Byzantine participant below the threshold suffices to corrupt the output.

---

### Recommendation

Apply the same `aggregated_output_check` pairing verification used in `protocol_pv.rs` to `do_ckd_coordinator` in `protocol.rs`. Alternatively, require all callers to use the PV variant (`ckd_pv`) which already has this guard. [7](#0-6) 

---

### Proof of Concept

1. Set up 3 participants `[P0, P1, P2]` with a shared key.
2. Run `ckd()` honestly for `P0` and `P2` with the full participant list `[P0, P1, P2]`.
3. Run `ckd()` for `P1` (Byzantine) with the truncated list `[P0, P1]` — this changes `P1`'s `lambda_i`.
4. Route all messages through the coordinator normally.
5. Assert that `ckd_output.unmask(app_sk) != hash_app_id_with_pk(&pk, &app_id) * msk`.

The assertion will hold because `P1`'s share was weighted by the wrong Lagrange coefficient, making the sum inconsistent. The `protocol.rs` coordinator returns this corrupted output without error.

### Citations

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol.rs (L54-61)
```rust
    for (_, participant_output) in
        recv_from_others::<CKDOutput>(&chan, waitpoint, &participants, me).await?
    {
        norm_big_y += participant_output.big_y();
        norm_big_c += participant_output.big_c();
    }
    let ckd_output = CKDOutput::new(norm_big_y, norm_big_c);
    Ok(Some(ckd_output))
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol.rs (L190-194)
```rust
    // Compute  λi := λi(0)
    let lambda_i = participants.lagrange::<BLS12381SHA256>(me)?;
    // Normalize Y and C into  (λi . Y , λi . C)
    let norm_big_y = big_y * lambda_i;
    let norm_big_c = big_c * lambda_i;
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L66-70)
```rust
    if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
        return Err(ProtocolError::AssertionFailed(
            "CKD output failed to verify".to_string(),
        ));
    }
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L222-236)
```rust
fn aggregated_output_check(
    output: &CKDOutput,
    app_pk: &PublicVerificationKey,
    public_key: &VerifyingKey,
    hash_point: &ElementG1,
) -> bool {
    if !check_valid_point_g1(output.big_c.into()) || !check_valid_point_g1(output.big_y.into()) {
        return false;
    }
    multi_miller_loop(&[
        (output.big_c, -ElementG2::generator()),
        (output.big_y, app_pk.pk2),
        (*hash_point, public_key.to_element()),
    ])
}
```

**File:** crates/threshold-signatures/src/protocol/helpers.rs (L15-24)
```rust
    let mut seen = ParticipantCounter::new(participants);
    seen.put(me);
    let mut messages = Vec::with_capacity(participants.others(me).count());

    while !seen.full() {
        let (from, msg) = chan.recv(waitpoint).await?;
        if seen.put(from) {
            messages.push((from, msg));
        }
    }
```

**File:** crates/threshold-signatures/src/confidential_key_derivation.rs (L53-55)
```rust
    pub fn unmask(&self, secret_scalar: Scalar) -> Signature {
        self.big_c - self.big_y * secret_scalar
    }
```
