# Q3427: MPC transport public-private blob downgrade in job_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript when public extraction is compared with signing output, reach `src/cbmpc/api/job_util.h` `validate_job_mp`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/job_util.h::validate_job_mp`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically scalar-detached public blob edited to look like a full signing key blob when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript when public extraction is compared with signing output. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/api/job_util.h` `validate_job_mp` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `src/cbmpc/api/job_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/api/job_util.h` `validate_job_mp` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
