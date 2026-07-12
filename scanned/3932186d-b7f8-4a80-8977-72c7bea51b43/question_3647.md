# Q3647: cb-mpc protocol malformed point acceptance in base_pki.h

## Question
Can an unprivileged attacker enter through `coinbase::api::schnorr_2p::sign` with key_blob, 32-byte digest, and malicious peer transcript after successful DKG and before signing, reach `include-internal/cbmpc/internal/crypto/base_pki.h` `open`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/base_pki.h::open`
- Entrypoint: `coinbase::api::schnorr_2p::sign via include/cbmpc/api/schnorr_2p.h`
- Attacker controls: key_blob, 32-byte digest, and malicious peer transcript; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::schnorr_2p::sign` in `include/cbmpc/api/schnorr_2p.h` with key_blob, 32-byte digest, and malicious peer transcript after successful DKG and before signing. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `include-internal/cbmpc/internal/crypto/base_pki.h` `open` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::schnorr_2p::sign` through `include-internal/cbmpc/internal/crypto/base_pki.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `include-internal/cbmpc/internal/crypto/base_pki.h` `open` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
