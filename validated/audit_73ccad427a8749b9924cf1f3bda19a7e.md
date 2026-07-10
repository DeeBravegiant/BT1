### Title
Byzantine Participant Can Bias CKD Output in Non-PV Protocol Path — (`crates/threshold-signatures/src/confidential_key_derivation/protocol.rs`)

### Summary

The non-public-verifiable CKD protocol (`protocol.rs`) has no integrity check on participant-contributed `CKDOutput` values. A single Byzantine participant can send an arbitrary `(big_y, big_c)` pair to the coordinator, causing the aggregated output to deviate from the expected invariant `big_C = msk·H(pk‖app_id) + big_Y·a` by an attacker-controlled additive offset. This path is live in production for `AppPublicKey`-type CKD requests.

---

### Finding Description

**Entrypoint**: A Byzantine MPC participant sends a crafted `CKDOutput` message to the coordinator during a CKD session initiated via `AppPublicKey` (non-PV) request type.

**Coordinator aggregation — no verification**

In `do_ckd_coordinator`, the coordinator simply sums all received contributions:

```rust
for (_, participant_output) in
    recv_from_others::<CKDOutput>(&chan, waitpoint, &participants, me).await?
{
    norm_big_y += participant_output.big_y();
    norm_big_c += participant_output.big_c();
}
let ckd_output = CKDOutput::new(norm_big_y, norm_big_c);
Ok(Some(ckd_output))
``` [1](#0-0) 

There is no pairing check, commitment, or any other verification that the received `big_c` and `big_y` values are consistent with the participant's actual key share.

**`recv_from_others` accepts only the first message per participant**

`ParticipantCounter::put` returns `false` for duplicates, so only the first message from each participant is processed:

```rust
while !seen.full() {
    let (from, msg) = chan.recv(waitpoint).await?;
    if seen.put(from) {
        messages.push((from, msg));
    }
}
``` [2](#0-1) 

This means a Byzantine participant's first (malicious) message is accepted and subsequent honest retries are dropped — but more importantly, the attacker has full control over what that first message contains.

**Attacker-controlled additive bias**

Each honest participant i computes:
- `norm_big_c_i = λ_i · (x_i · H(pk‖app_id) + y_i · A)`
- `norm_big_y_i = λ_i · y_i · G`

A Byzantine participant j instead sends `norm_big_c_j' = norm_big_c_j + δ` for any group element `δ` of their choice (e.g., `2 · norm_big_c_j`). The coordinator's final output becomes:

```
big_C_final = correct_big_C + δ
big_Y_final = correct_big_Y  (if big_y is kept honest)
```

When the client unmasks: `big_C_final − a · big_Y_final = msk · H(pk‖app_id) + δ`

The derived key is `msk · H + δ` instead of `msk · H`. The attacker knows `δ` exactly (they chose it), but cannot predict `msk · H` since they don't know `msk`. The protocol returns `Ok(Some(ckd_output))` with no error.

**The PV variant has the fix; the non-PV variant does not**

`protocol_pv.rs` performs a pairing-based aggregated output check after summing:

```rust
if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
    return Err(ProtocolError::AssertionFailed(
        "CKD output failed to verify".to_string(),
    ));
}
``` [3](#0-2) 

`protocol.rs` has no equivalent check. [1](#0-0) 

**Both paths are live in production**

`crates/node/src/providers/ckd/sign.rs` dispatches to `protocol::ckd` (non-PV, unprotected) for `AppPublicKey` requests and to `ckd_pv` (protected) for `AppPublicKeyPV` requests:

```rust
dtos::CKDAppPublicKey::AppPublicKey(pk) => {
    let protocol = ckd(...)?;
    run_protocol("ckd", channel, protocol).await?
}
dtos::CKDAppPublicKey::AppPublicKeyPV(pv) => {
    let protocol = ckd_pv(...)?;
    run_protocol("ckd_pv", channel, protocol).await?
}
``` [4](#0-3) 

---

### Impact Explanation

A single Byzantine participant (strictly below threshold) can cause the coordinator to output a CKD result that does not satisfy the protocol invariant. The derived key is `msk · H(pk‖app_id) + δ` for an attacker-chosen `δ`. The attacker knows `δ` but not the final key. Downstream consequences:

- Any client operation relying on the correct derived key (decryption, verification) silently fails or produces wrong results.
- The protocol returns `Ok` with no indication of tampering.
- The attacker can repeatedly bias different sessions with different `δ` values, making the output unpredictable and unusable.

This breaks the CKD correctness invariant for all `AppPublicKey`-type requests without requiring threshold collusion.

---

### Likelihood Explanation

Any one of the MPC participants running the non-PV CKD path can mount this attack. It requires no special access beyond being a legitimate protocol participant. The attack is trivially executable: the participant simply sends a modified `CKDOutput` message. The `AppPublicKey` path is a live production code path.

---

### Recommendation

Add an aggregated output check to `do_ckd_coordinator` in `protocol.rs`, analogous to the check already present in `protocol_pv.rs`. For the non-PV case (where `app_pk` is only a G1 element, not a G1+G2 pair), a suitable check would verify that `big_C − a · big_Y = msk · H(pk‖app_id)` using the known public key and hash point, or alternatively require participants to provide zero-knowledge proofs of correct share contribution.

---

### Proof of Concept

Run CKD with 3 participants where participant[1] sends `big_c = 2 · correct_big_c`:

1. Compute `correct_norm_big_c_1 = λ_1 · (x_1 · H + y_1 · A)` honestly.
2. Participant[1] sends `CKDOutput::new(norm_big_y_1, 2 · norm_big_c_1)` to coordinator.
3. Coordinator sums: `big_C_final = correct_big_C + norm_big_c_1`.
4. Client unmasks: `big_C_final − a · big_Y = msk · H + λ_1 · x_1 · H = (msk + λ_1 · x_1) · H`.
5. Assert `ckd_output.unmask(app_sk) ≠ hash_app_id_with_pk(&pk, &app_id) * msk` — deviation equals exactly `λ_1 · x_1 · H`, which the attacker knows.

The existing test `test_ckd` in `protocol.rs` confirms the honest invariant holds; a modified version with a tampered participant message would demonstrate the deviation. [5](#0-4)

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

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol.rs (L226-276)
```rust
    fn test_ckd() {
        let mut rng = MockCryptoRng::seed_from_u64(42);

        let app_id = AppId::try_from(b"Near App").unwrap();
        let app_sk = Scalar::random(&mut rng);
        let app_pk = ElementG1::generator() * app_sk;

        let participants = generate_participants(3);
        let coordinator = *participants
            .choose(&mut rng)
            .expect("participant list is not empty");

        let (f, pk) = generate_test_keys(participants.len() - 1, &mut rng);
        let msk = f.eval_at_zero().unwrap().0;

        let mut protocols: GenProtocol<CKDOutputOption> = Vec::with_capacity(participants.len());
        for p in &participants {
            let rng_p = MockCryptoRng::seed_from_u64(rng.next_u64());
            let key_pair = make_keygen_output(&f, &pk, *p);

            let protocol = ckd(
                &participants,
                coordinator,
                *p,
                key_pair,
                app_id.clone(),
                app_pk,
                rng_p,
            )
            .unwrap();

            protocols.push((*p, Box::new(protocol)));
        }

        let result = run_protocol(protocols).unwrap();

        // test one single some for the coordinator
        let ckd_output = check_one_coordinator_output(result, coordinator).unwrap();

        // compute msk . H(pk, app_id)
        let confidential_key = ckd_output.unmask(app_sk);

        // H(pk || app_id) * msk
        let expected_confidential_key = hash_app_id_with_pk(&pk, &app_id) * msk;

        assert_eq!(
            confidential_key, expected_confidential_key,
            "Keys should be equal"
        );
        insta::assert_json_snapshot!(ckd_output);
    }
```

**File:** crates/threshold-signatures/src/protocol/helpers.rs (L19-24)
```rust
    while !seen.full() {
        let (from, msg) = chan.recv(waitpoint).await?;
        if seen.put(from) {
            messages.push((from, msg));
        }
    }
```

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L66-70)
```rust
    if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
        return Err(ProtocolError::AssertionFailed(
            "CKD output failed to verify".to_string(),
        ));
    }
```

**File:** crates/node/src/providers/ckd/sign.rs (L151-178)
```rust
        let result = match self.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(pk) => {
                let protocol = ckd(
                    cs_participants.as_slice(),
                    leader,
                    my_id,
                    self.keygen_output,
                    app_id,
                    ElementG1::try_from(&pk)?,
                    OsRng,
                )?;
                run_protocol("ckd", channel, protocol).await?
            }
            dtos::CKDAppPublicKey::AppPublicKeyPV(pv) => {
                let pk1 = ElementG1::try_from(&pv.pk1)?;
                let pk2 = ElementG2::try_from(&pv.pk2)?;
                let protocol = ckd_pv(
                    cs_participants.as_slice(),
                    leader,
                    my_id,
                    self.keygen_output,
                    app_id,
                    PublicVerificationKey::new(pk1, pk2),
                    OsRng,
                )?;
                run_protocol("ckd_pv", channel, protocol).await?
            }
        };
```
