# Q1839: ECC validation malformed point acceptance in base_ecc_secp256k1.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label while two sessions run concurrently, reach `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_ecc_secp256k1.cpp::pub_from_der`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label while two sessions run concurrently. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `src/cbmpc/crypto/base_ecc_secp256k1.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
