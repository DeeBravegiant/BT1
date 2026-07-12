# Q1553: MPC transport public-private blob downgrade in job_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::hd_keyset_eddsa_2p::derive_eddsa_2p_keys` with keyset_blob, hardened_path, and malicious derivation transcript while two sessions run concurrently, reach `src/cbmpc/api/job_util.h` `validate_job_mp`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/job_util.h::validate_job_mp`
- Entrypoint: `coinbase::api::hd_keyset_eddsa_2p::derive_eddsa_2p_keys via include/cbmpc/api/hd_keyset_eddsa_2p.h`
- Attacker controls: keyset_blob, hardened_path, and malicious derivation transcript; specifically scalar-detached public blob edited to look like a full signing key blob while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::hd_keyset_eddsa_2p::derive_eddsa_2p_keys` in `include/cbmpc/api/hd_keyset_eddsa_2p.h` with keyset_blob, hardened_path, and malicious derivation transcript while two sessions run concurrently. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/api/job_util.h` `validate_job_mp` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::hd_keyset_eddsa_2p::derive_eddsa_2p_keys` through `src/cbmpc/api/job_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/api/job_util.h` `validate_job_mp` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
