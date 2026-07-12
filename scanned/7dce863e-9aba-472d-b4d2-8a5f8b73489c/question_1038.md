# Q1038: cb-mpc protocol malformed point acceptance in base_hash.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages during the first accepted protocol run, reach `src/cbmpc/crypto/base_hash.cpp` `base_hash module`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_hash.cpp::base_hash module`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages during the first accepted protocol run. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/crypto/base_hash.cpp` `base_hash module` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/crypto/base_hash.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/crypto/base_hash.cpp` `base_hash module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
