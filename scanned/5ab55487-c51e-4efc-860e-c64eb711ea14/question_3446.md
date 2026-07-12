# Q3446: ECC validation malformed point acceptance in base_ecc_secp256k1.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript after refresh but before public-key export, reach `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_ecc_secp256k1.cpp::ossl_ecdsa_verify`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript after refresh but before public-key export. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `src/cbmpc/crypto/base_ecc_secp256k1.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
