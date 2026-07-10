### Title
Zero-Hash ECDSA Payload Accepted by Contract but Permanently Rejected by DamgardEtAl Signing Nodes, Causing Presignature Drain and Request-Lifecycle Deadlock - (File: crates/contract/src/lib.rs)

---

### Summary

The `sign` contract method validates ECDSA payloads for both `Protocol::CaitSith` and `Protocol::DamgardEtAl` by checking only that the raw bytes decode to a valid `k256::Scalar` (i.e., the value is less than the field order). A zero payload `[0u8; 32]` satisfies this check because `0 < p`. However, the `robust_ecdsa::sign` function used by DamgardEtAl nodes explicitly rejects `msg_hash == 0` as a security requirement against split-view attacks. The contract's own comment states the validation exists precisely so the contract fails "in an identical way" to the MPC nodes — but it does not. Any unprivileged caller can submit a zero-hash request targeting a DamgardEtAl domain, which the contract enqueues, the nodes always reject, a presignature is consumed, and the request sits pending until the ~200-block yield-resume timeout fires.

---

### Finding Description

**Contract-side validation (permissive):**

In `crates/contract/src/lib.rs`, the `sign` method validates ECDSA payloads for both CaitSith and DamgardEtAl with a single shared check:

```rust
Protocol::CaitSith | Protocol::DamgardEtAl => {
    let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
    k256::Scalar::from_repr(hash.into())
        .into_option()
        .expect("Ecdsa payload cannot be converted to Scalar");
}
```

`k256::Scalar::from_repr([0u8; 32])` returns `Some(Scalar::ZERO)` because `0` is in the valid range `[0, p)`. The zero scalar passes this check, the yield-resume promise is created, and the request is stored in `pending_signature_requests`. [1](#0-0) 

The comment immediately above this block states the intent:

> "It's important we fail here because the MPC nodes will fail in an identical way. This allows users to get the error message." [2](#0-1) 

**Node-side validation (strict):**

In `crates/threshold-signatures/src/ecdsa/robust_ecdsa/sign.rs`, the `sign` function used by DamgardEtAl nodes explicitly rejects a zero message hash:

```rust
if bool::from(msg_hash.is_zero()) {
    return Err(InitializationError::BadParameters(
        "msg_hash cannot be 0 to avoid potential split view attacks".to_string(),
    ));
}
``` [3](#0-2) 

This is a documented security requirement — signing with `h = 0` enables a related algebraic split-view attack in the modified DJNPO20 scheme. [4](#0-3) 

**Presignature consumed before the zero-check fires:**

In `crates/node/src/providers/robust_ecdsa/sign.rs`, the leader node takes a presignature from the store *before* invoking the signing protocol:

```rust
let (presignature_id, presignature) = domain_data.presignature_store.take_owned().await;
``` [5](#0-4) 

The `SignComputation` is then constructed with `msg_hash: msg_hash.into()` and dispatched. The `robust_ecdsa::sign` initialization check fires inside `perform_leader_centric_computation`, after the presignature has already been removed from the pool. The presignature is permanently consumed. [6](#0-5) 

**Timeout path:**

Because no node can produce a valid response for a zero-hash DamgardEtAl request, the yield-resume promise is never resolved by `respond`. After `REQUEST_EXPIRATION_BLOCKS = 200` blocks, the runtime fires `return_signature_and_clean_state_on_success` with `Err(PromiseError::Failed)`, which pops the yield and calls `fail_on_timeout`. [7](#0-6) 

---

### Impact Explanation

**Medium.** This is a request-lifecycle manipulation that breaks the production safety invariant explicitly documented in the contract source: the contract should reject any payload that the MPC nodes will reject. For DamgardEtAl domains, it does not. Consequences:

1. **Presignature drain**: Each zero-hash request causes the leader node to irreversibly consume one presignature from the pool. Presignatures are expensive to generate (three-round offline protocol). An attacker paying 1 yoctonear per request can systematically drain the DamgardEtAl presignature pool, delaying or blocking legitimate signing requests until the pool refills.
2. **Request-lifecycle corruption**: The contract enqueues a request it guarantees will never be fulfilled, holding a yield-resume slot for ~200 blocks. Legitimate duplicate requests for the same key are queued behind it.
3. **Silent failure mode**: Users receive a generic timeout error after ~200 blocks rather than the immediate, descriptive error the contract comment promises.

---

### Likelihood Explanation

**High.** The entry path requires no privilege: any account with 1 yoctonear can call `sign` with `payload_v2: {"Ecdsa": "0000...0000"}` targeting a DamgardEtAl domain. The zero payload is a single, trivially known value. No threshold collusion, key material, or operator access is needed.

---

### Recommendation

Add a zero-scalar check specifically for `Protocol::DamgardEtAl` inside the `sign` method, mirroring the node-side guard:

```rust
Protocol::DamgardEtAl => {
    let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
    let scalar = k256::Scalar::from_repr(hash.into())
        .into_option()
        .expect("Ecdsa payload cannot be converted to Scalar");
    if bool::from(scalar.is_zero()) {
        env::panic_str("Ecdsa payload cannot be zero for DamgardEtAl (split-view attack prevention)");
    }
}
```

This makes the contract's validation identical to the node's, fulfilling the stated design intent and preventing zero-hash requests from ever reaching the pending queue.

---

### Proof of Concept

1. Deploy or connect to a NEAR MPC contract with a `DamgardEtAl` domain (e.g., `domain_id = N`).
2. Call `sign` with:
   ```json
   {
     "request": {
       "payload_v2": { "Ecdsa": "0000000000000000000000000000000000000000000000000000000000000000" },
       "path": "any-path",
       "domain_id": N
     }
   }
   ```
   attaching 1 yoctonear. The call succeeds and a yield-resume promise is created.
3. Observe that no MPC node submits a `respond` call: every node's `robust_ecdsa::sign` returns `Err(BadParameters("msg_hash cannot be 0..."))` and the consumed presignature is not returned to the pool.
4. After ~200 blocks, `return_signature_and_clean_state_on_success` fires with `Err(PromiseError::Failed)` and the user receives a timeout error.
5. Repeat step 2 in a loop to drain the presignature pool. Legitimate signing requests submitted concurrently will stall waiting for new presignatures to be generated.

### Citations

**File:** crates/contract/src/lib.rs (L359-377)
```rust
        // ensure the signer sent a valid signature request
        // It's important we fail here because the MPC nodes will fail in an identical way.
        // This allows users to get the error message
        match domain_config.protocol {
            Protocol::CaitSith | Protocol::DamgardEtAl => {
                let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
                k256::Scalar::from_repr(hash.into())
                    .into_option()
                    .expect("Ecdsa payload cannot be converted to Scalar");
            }
            Protocol::Frost => {
                request.payload.as_eddsa().expect("Payload is not EdDSA");
            }
            Protocol::ConfidentialKeyDerivation => {
                env::panic_str(
                    "ConfidentialKeyDerivation is not supported for signature responses",
                );
            }
        }
```

**File:** crates/threshold-signatures/src/ecdsa/robust_ecdsa/sign.rs (L100-104)
```rust
    if bool::from(msg_hash.is_zero()) {
        return Err(InitializationError::BadParameters(
            "msg_hash cannot be 0 to avoid potential split view attacks".to_string(),
        ));
    }
```

**File:** crates/threshold-signatures/docs/ecdsa/robust_ecdsa/signing.md (L179-181)
```markdown
4. **Do not sign with $h = 0$** (the zero message hash).
   This input enables a related algebraic split-view attack in the modified scheme when
   $N_1 > 2t + 1$.
```

**File:** crates/node/src/providers/robust_ecdsa/sign.rs (L32-33)
```rust
        let (presignature_id, presignature) = domain_data.presignature_store.take_owned().await;
        let participants = presignature.participants.clone();
```

**File:** crates/node/src/providers/robust_ecdsa/sign.rs (L50-69)
```rust
        let (signature, public_key) = SignComputation {
            keygen_out: domain_data.keyshare,
            max_malicious: robust_ecdsa_threshold,
            presign_out: presignature.presignature,
            msg_hash: msg_hash.into(),
            tweak: sign_request.tweak,
            entropy: sign_request.entropy,
        }
        .perform_leader_centric_computation(
            channel,
            Duration::from_secs(self.config.signature.timeout_sec),
        )
        .await
        .inspect_err(|_| {
            participants.iter().for_each(|id| {
                metrics::PARTICIPANT_TOTAL_TIMES_SEEN_IN_FAILED_SIGNATURE_COMPUTATION_LEADER
                    .with_label_values(&[&id.raw().to_string()])
                    .inc();
            });
        })?;
```

**File:** crates/node/src/requests/queue.rs (L31-33)
```rust
/// The number of blocks after which a request is assumed to have timed out.
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
