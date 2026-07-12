# Q3487: EdDSA fixed-buffer exactness gap in eddsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript while one malicious peer deviates and one honest party is unmodified, reach `include/cbmpc/api/eddsa_mp.h` `get_public_key_compressed`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/eddsa_mp.h::get_public_key_compressed`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript while one malicious peer deviates and one honest party is unmodified. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `include/cbmpc/api/eddsa_mp.h` `get_public_key_compressed` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `include/cbmpc/api/eddsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `include/cbmpc/api/eddsa_mp.h` `get_public_key_compressed` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
