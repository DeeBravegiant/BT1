# Q1616: PVE fixed-buffer exactness gap in pve_base_pke.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares when public extraction is compared with signing output, reach `include/cbmpc/api/pve_base_pke.h` `decrypt`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/pve_base_pke.h::decrypt`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares when public extraction is compared with signing output. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `include/cbmpc/api/pve_base_pke.h` `decrypt` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `include/cbmpc/api/pve_base_pke.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `include/cbmpc/api/pve_base_pke.h` `decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
