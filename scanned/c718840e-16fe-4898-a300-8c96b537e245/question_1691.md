# Q1691: PVE fixed-buffer exactness gap in pve_batch_single_recipient.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares when labels or sids are reused across supported flows, reach `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/pve_batch_single_recipient.cpp::decrypt_batch`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares when labels or sids are reused across supported flows. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `src/cbmpc/api/pve_batch_single_recipient.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/api/pve_batch_single_recipient.cpp` `decrypt_batch` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
