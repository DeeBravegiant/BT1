# Q3849: EdDSA public-private blob downgrade in base_eddsa.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript during the first accepted protocol run, reach `src/cbmpc/crypto/base_eddsa.cpp` `from_bin`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_eddsa.cpp::from_bin`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically scalar-detached public blob edited to look like a full signing key blob during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript during the first accepted protocol run. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/crypto/base_eddsa.cpp` `from_bin` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `src/cbmpc/crypto/base_eddsa.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/crypto/base_eddsa.cpp` `from_bin` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
