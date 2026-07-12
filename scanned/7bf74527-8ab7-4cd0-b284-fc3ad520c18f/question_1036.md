# Q1036: ECC validation public-private blob downgrade in base_ecc_secp256k1.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages when labels or sids are reused across supported flows, reach `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `verify`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_ecc_secp256k1.cpp::verify`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically scalar-detached public blob edited to look like a full signing key blob when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages when labels or sids are reused across supported flows. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `verify` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/crypto/base_ecc_secp256k1.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
