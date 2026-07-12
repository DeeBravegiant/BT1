# Q1599: PVE fixed-buffer exactness gap in pve_batch.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::decrypt_batch` with dk, ek, ciphertext, and label after successful DKG and before signing, reach `src/cbmpc/protocol/pve_batch.cpp` `decrypt`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/pve_batch.cpp::decrypt`
- Entrypoint: `coinbase::api::pve::decrypt_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: dk, ek, ciphertext, and label; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::pve::decrypt_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with dk, ek, ciphertext, and label after successful DKG and before signing. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/protocol/pve_batch.cpp` `decrypt` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::decrypt_batch` through `src/cbmpc/protocol/pve_batch.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/protocol/pve_batch.cpp` `decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
