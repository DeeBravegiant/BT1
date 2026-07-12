# Q1577: PVE fixed-buffer exactness gap in base_rsa.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_batch` with ek, ciphertext, Q vector, and label while two sessions run concurrently, reach `src/cbmpc/crypto/base_rsa.cpp` `verify_pkcs1`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_rsa.cpp::verify_pkcs1`
- Entrypoint: `coinbase::api::pve::verify_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: ek, ciphertext, Q vector, and label; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with ek, ciphertext, Q vector, and label while two sessions run concurrently. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/crypto/base_rsa.cpp` `verify_pkcs1` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_batch` through `src/cbmpc/crypto/base_rsa.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/crypto/base_rsa.cpp` `verify_pkcs1` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
