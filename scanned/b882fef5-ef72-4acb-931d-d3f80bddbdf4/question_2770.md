# Q2770: serialization/core malformed point acceptance in convert.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript during threshold combine with a minimal quorum, reach `src/cbmpc/core/convert.cpp` `convert module`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/core/convert.cpp::convert module`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript during threshold combine with a minimal quorum. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/core/convert.cpp` `convert module` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The serialization/core path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `src/cbmpc/core/convert.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/core/convert.cpp` `convert module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
