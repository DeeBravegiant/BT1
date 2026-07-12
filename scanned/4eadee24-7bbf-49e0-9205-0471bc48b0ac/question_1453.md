# Q1453: ECDSA-2PC fixed-buffer exactness gap in ecdsa_2p.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows, reach `src/cbmpc/protocol/ecdsa_2p.cpp` `step4_p2_output`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_2p.cpp::step4_p2_output`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/protocol/ecdsa_2p.cpp` `step4_p2_output` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/protocol/ecdsa_2p.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/protocol/ecdsa_2p.cpp` `step4_p2_output` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
