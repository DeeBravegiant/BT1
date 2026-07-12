# Q1414: EdDSA fixed-buffer exactness gap in eddsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript when public extraction is compared with signing output, reach `src/cbmpc/api/eddsa2pc.cpp` `blob_to_key`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/eddsa2pc.cpp::blob_to_key`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript when public extraction is compared with signing output. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/api/eddsa2pc.cpp` `blob_to_key` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `src/cbmpc/api/eddsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/api/eddsa2pc.cpp` `blob_to_key` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
