# Q1716: access-structure curve binding drift in secret_sharing.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript when parties disagree on recipient or quorum ordering, reach `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_exponent_recursive`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/secret_sharing.cpp::reconstruct_exponent_recursive`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically a curve_id paired with points or scalars from another supported curve when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript when parties disagree on recipient or quorum ordering. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_exponent_recursive` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `src/cbmpc/crypto/secret_sharing.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_exponent_recursive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
