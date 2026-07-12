# Q3345: ZK proof public-private blob downgrade in zk_ec.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label while one malicious peer deviates and one honest party is unmodified, reach `src/cbmpc/zk/zk_ec.cpp` `verify`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_ec.cpp::verify`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically scalar-detached public blob edited to look like a full signing key blob while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label while one malicious peer deviates and one honest party is unmodified. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/zk/zk_ec.cpp` `verify` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/zk/zk_ec.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/zk/zk_ec.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
