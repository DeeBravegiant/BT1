# Q487: BIP340 Schnorr malformed point acceptance in schnorr2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::schnorr_mp::sign_ac` with ac key blob, access_structure, digest, receiver, and peer messages when the same caller alternates valid and mutated blobs, reach `src/cbmpc/api/schnorr2pc.cpp` `dkg`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/schnorr2pc.cpp::dkg`
- Entrypoint: `coinbase::api::schnorr_mp::sign_ac via include/cbmpc/api/schnorr_mp.h`
- Attacker controls: ac key blob, access_structure, digest, receiver, and peer messages; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes when the same caller alternates valid and mutated blobs
- Exploit idea: Start from supported public API `coinbase::api::schnorr_mp::sign_ac` in `include/cbmpc/api/schnorr_mp.h` with ac key blob, access_structure, digest, receiver, and peer messages when the same caller alternates valid and mutated blobs. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/api/schnorr2pc.cpp` `dkg` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The BIP340 Schnorr path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::schnorr_mp::sign_ac` through `src/cbmpc/api/schnorr2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/api/schnorr2pc.cpp` `dkg` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
