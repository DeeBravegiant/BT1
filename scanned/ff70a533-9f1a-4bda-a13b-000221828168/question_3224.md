# Q3224: PVE malformed point acceptance in pve_batch_ac.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_ac` with ciphertext, expected Qs, access_structure, leaf keys, and label when public extraction is compared with signing output, reach `include/cbmpc/api/pve_batch_ac.h` `encrypt_ac`, and use non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes to bypass the requirement that all peer points are canonicalized and curve/subgroup checked before arithmetic, causing an invalid public share, commitment, or proof point is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/pve_batch_ac.h::encrypt_ac`
- Entrypoint: `coinbase::api::pve::verify_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, expected Qs, access_structure, leaf keys, and label; specifically non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, expected Qs, access_structure, leaf keys, and label when public extraction is compared with signing output. The malicious side supplies non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes. Investigate whether `include/cbmpc/api/pve_batch_ac.h` `encrypt_ac` assumes all peer points are canonicalized and curve/subgroup checked before arithmetic was already enforced and therefore lets an invalid public share, commitment, or proof point is accepted.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_ac` through `include/cbmpc/api/pve_batch_ac.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate non-canonical compressed point, infinity encoding, low-order Ed25519 point, or off-curve SEC1 bytes; assert rejection before `include/cbmpc/api/pve_batch_ac.h` `encrypt_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
