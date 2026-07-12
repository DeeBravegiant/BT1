# Q658: PVE malformed point acceptance in pve.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_ac` with ciphertext, expected Qs, access_structure, leaf keys, and label after successful DKG and before signing, reach `src/cbmpc/protocol/pve.cpp` `encrypt`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/pve.cpp::encrypt`
- Entrypoint: `coinbase::api::pve::verify_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, expected Qs, access_structure, leaf keys, and label; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, expected Qs, access_structure, leaf keys, and label after successful DKG and before signing. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `src/cbmpc/protocol/pve.cpp` `encrypt` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_ac` through `src/cbmpc/protocol/pve.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `src/cbmpc/protocol/pve.cpp` `encrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
