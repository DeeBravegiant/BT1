# Q2826: TDH2 curve binding drift in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label after refresh but before public-key export, reach `include/cbmpc/api/tdh2.h` `dkg_additive`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::dkg_additive`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically a curve_id paired with points or scalars from another supported curve after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label after refresh but before public-key export. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `include/cbmpc/api/tdh2.h` `dkg_additive` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `include/cbmpc/api/tdh2.h` `dkg_additive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
