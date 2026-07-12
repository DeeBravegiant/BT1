# Q1595: cb-mpc protocol curve binding drift in ot.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point when the same caller alternates valid and mutated blobs, reach `src/cbmpc/protocol/ot.cpp` `step2_S2R`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ot.cpp::step2_S2R`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically a curve_id paired with points or scalars from another supported curve when the same caller alternates valid and mutated blobs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point when the same caller alternates valid and mutated blobs. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/protocol/ot.cpp` `step2_S2R` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `src/cbmpc/protocol/ot.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/protocol/ot.cpp` `step2_S2R` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
