# Q753: PVE scalar width confusion in pve_batch_single_recipient.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::decrypt_batch` with dk, ek, ciphertext, and label after successful DKG and before signing, reach `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch_ecies_p256_hsm`, and use zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding to bypass the requirement that scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction, causing a substituted scalar becomes usable key material and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/pve_batch_single_recipient.cpp::decrypt_batch_ecies_p256_hsm`
- Entrypoint: `coinbase::api::pve::decrypt_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: dk, ek, ciphertext, and label; specifically zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::pve::decrypt_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with dk, ek, ciphertext, and label after successful DKG and before signing. The malicious side supplies zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding. Investigate whether `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch_ecies_p256_hsm` assumes scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction was already enforced and therefore lets a substituted scalar becomes usable key material.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::decrypt_batch` through `src/cbmpc/api/pve_batch_single_recipient.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding; assert rejection before `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch_ecies_p256_hsm` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
