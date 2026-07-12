# Q2565: MPC transport malformed point acceptance in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript while two sessions run concurrently, reach `include/cbmpc/core/job.h` `receive`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::receive`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript while two sessions run concurrently. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `include/cbmpc/core/job.h` `receive` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `include/cbmpc/core/job.h` `receive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
