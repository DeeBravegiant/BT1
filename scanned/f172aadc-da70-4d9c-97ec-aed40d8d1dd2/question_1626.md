# Q1626: serialization/core curve binding drift in buf256.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label during threshold combine with a minimal quorum, reach `include/cbmpc/core/buf256.h` `buf256 module`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/buf256.h::buf256 module`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically a curve_id paired with points or scalars from another supported curve during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label during threshold combine with a minimal quorum. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `include/cbmpc/core/buf256.h` `buf256 module` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The serialization/core path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `include/cbmpc/core/buf256.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `include/cbmpc/core/buf256.h` `buf256 module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
