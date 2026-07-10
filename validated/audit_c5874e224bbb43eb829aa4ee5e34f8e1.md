### Title
Missing Aggregated-Output Verification in Non-PV CKD Coordinator Allows Byzantine Participant to Inject Replayed Shares — (`crates/threshold-signatures/src/confidential_key_derivation/protocol.rs`)

---

### Summary

The privately-verifiable CKD coordinator (`do_ckd_coordinator` in `protocol.rs`) aggregates participant `CKDOutput` shares with no post-aggregation correctness check. A single Byzantine participant below the signing threshold can replay a `CKDOutput` from any prior CKD run (different `app_id`, `app_pk`, or `participants`), causing the coordinator to return a cryptographically invalid output for the current request. The app silently derives the wrong key. The publicly-verifiable variant (`protocol_pv.rs`) already contains the fix; the non-PV path does not.

---

### Finding Description

**Root cause — `protocol.rs` `do_ckd_coordinator` (lines 39–62):**

The coordinator collects one `CKDOutput` per participant via `recv_from_others`, then blindly adds the group elements together:

```rust
for (_, participant_output) in
    recv_from_others::<CKDOutput>(&chan, waitpoint, &participants, me).await?
{
    norm_big_y += participant_output.big_y();
    norm_big_c += participant_output.big_c();
}
let ckd_output = CKDOutput::new(norm_big_y, norm_big_c);
Ok(Some(ckd_output))   // returned with no verification
``` [1](#0-0) 

`recv_from_others` only enforces that exactly one message arrives per listed participant; it performs zero semantic validation of the payload:

```rust
while !seen.full() {
    let (from, msg) = chan.recv(waitpoint).await?;
    if seen.put(from) {
        messages.push((from, msg));
    }
}
``` [2](#0-1) 

A `CKDOutput` is just two `ElementG1` points (`big_y`, `big_c`) with no binding to `app_id`, `app_pk`, or the current participant set. Nothing in the deserialization path or the aggregation loop checks that the received points were derived from the correct inputs.

**Contrast with the fixed path — `protocol_pv.rs` `do_ckd_coordinator` (lines 39–73):**

After the identical aggregation loop, the PV coordinator calls `aggregated_output_check`, which enforces the pairing equation `e(C, G₂) = e(Y, app_pk₂) · e(H(pk‖app_id), vk)`:

```rust
if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
    return Err(ProtocolError::AssertionFailed(
        "CKD output failed to verify".to_string(),
    ));
}
``` [3](#0-2) 

This check is structurally absent from `protocol.rs`.

**Production dispatch — `crates/node/src/providers/ckd/sign.rs` (lines 151–162):**

The non-PV path is live in production for any `AppPublicKey` (single G1 point) request:

```rust
dtos::CKDAppPublicKey::AppPublicKey(pk) => {
    let protocol = ckd(
        cs_participants.as_slice(), leader, my_id,
        self.keygen_output, app_id,
        ElementG1::try_from(&pk)?, OsRng,
    )?;
    run_protocol("ckd", channel, protocol).await?
}
``` [4](#0-3) 

---

### Impact Explanation

A Byzantine participant sends a `CKDOutput` it computed during a previous CKD run (for a different `app_id` or `app_pk`). The coordinator aggregates it with the honest shares for the current run. The resulting `(Y, C)` pair satisfies no valid pairing relation for the current `(app_id, app_pk, msk)` tuple. When the app calls `unmask(app_sk)`, it recovers `C − a·Y`, which is not `H(pk‖app_id)·msk`. The app silently derives the wrong key.

Because the non-PV variant has no G2 component for `app_pk`, the coordinator cannot perform the pairing check that `protocol_pv.rs` uses. The app is the only entity that could detect the error — but only if it has an independent oracle for the expected key, which it does not in the standard CKD flow. The determinism invariant ("same `app_id` + same `app_pk` always yields the same key") is broken for the affected request.

Scope match: **Medium — request-lifecycle manipulation breaking a production safety invariant** (deterministic CKD output bound to `(app_id, app_pk, participants)`).

---

### Likelihood Explanation

- Requires one Byzantine participant (below threshold) who has previously participated in any CKD run — a realistic condition for a long-running MPC network.
- No threshold collusion, no key leakage, no network-level attack needed.
- The attack is single-round and requires only replaying a previously observed message.
- The non-PV `AppPublicKey` variant is the legacy/default path and is actively used in production.

---

### Recommendation

Add a post-aggregation correctness check to `do_ckd_coordinator` in `protocol.rs`. Because the non-PV variant lacks a G2 component for `app_pk`, the exact pairing equation from `protocol_pv.rs` cannot be reused directly. Options:

1. **Deprecate the non-PV path** and require all callers to use `AppPublicKeyPV` (which already has the fix). The contract already supports both variants; the non-PV path can be gated or removed.
2. **Add a weaker consistency check** in `protocol.rs`: each participant includes a commitment (e.g., a hash of `(app_id, app_pk, participants, epoch)`) alongside their `CKDOutput`, and the coordinator rejects any share whose commitment does not match the current session parameters. This does not require pairing operations and is sufficient to prevent cross-session replay. [5](#0-4) [6](#0-5) 

---

### Proof of Concept

```rust
// Integration test sketch (non-PV protocol.rs path)
let app_id_a = AppId::try_from(b"app-a").unwrap();
let app_id_b = AppId::try_from(b"app-b").unwrap();
let app_sk = Scalar::random(&mut rng);
let app_pk = ElementG1::generator() * app_sk;

// Run CKD for app_id_a; Byzantine participant P1 saves its own CKDOutput.
let saved_output: CKDOutput = /* P1's share from run A */;

// Now run CKD for app_id_b.
// P1 (Byzantine) sends `saved_output` (from run A) instead of its correct share.
// Coordinator aggregates without verification and returns a CKDOutput.
let wrong_ckd = coordinator_result; // aggregated with replayed share

// Unmask: recovers wrong key
let derived_key = wrong_ckd.unmask(app_sk);

// Expected key for app_id_b
let expected = hash_app_id_with_pk(&pk, &app_id_b) * msk;

assert_ne!(derived_key, expected); // passes — wrong key silently returned
```

The test asserts that the coordinator returns a `CKDOutput` that does not satisfy the expected key equation for `app_id_b`, demonstrating the invariant break without any threshold collusion.

### Citations

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol.rs (L39-62)
```rust
async fn do_ckd_coordinator(
    mut chan: SharedChannel,
    participants: ParticipantList,
    me: Participant,
    key_pair: &KeygenOutput,
    app_id: &AppId,
    app_pk: PublicKey,
    rng: &mut impl CryptoRngCore,
) -> Result<CKDOutputOption, ProtocolError> {
    let (mut norm_big_y, mut norm_big_c) =
        compute_signature_share(&participants, me, key_pair, app_id, app_pk, rng)?;

    // Receive everyone's inputs and add them together
    let waitpoint = chan.next_waitpoint();

    for (_, participant_output) in
        recv_from_others::<CKDOutput>(&chan, waitpoint, &participants, me).await?
    {
        norm_big_y += participant_output.big_y();
        norm_big_c += participant_output.big_c();
    }
    let ckd_output = CKDOutput::new(norm_big_y, norm_big_c);
    Ok(Some(ckd_output))
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

**File:** crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs (L64-70)
```rust
    let ckd_output = CKDOutput::new(norm_big_y, norm_big_c);

    if !aggregated_output_check(&ckd_output, app_pk, &key_pair.public_key, &hash_point) {
        return Err(ProtocolError::AssertionFailed(
            "CKD output failed to verify".to_string(),
        ));
    }
```

**File:** crates/node/src/providers/ckd/sign.rs (L152-163)
```rust
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
```
