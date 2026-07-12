# Q1815: EdDSA public-private blob downgrade in eddsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages while two sessions run concurrently, reach `src/cbmpc/api/eddsa2pc.cpp` `dkg`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/eddsa2pc.cpp::dkg`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically scalar-detached public blob edited to look like a full signing key blob while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages while two sessions run concurrently. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/api/eddsa2pc.cpp` `dkg` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/api/eddsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/api/eddsa2pc.cpp` `dkg` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
