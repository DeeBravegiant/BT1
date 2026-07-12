# Q1075: ECDSA-MP error-state confusion in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point after refresh but before public-key export, reach `include/cbmpc/api/ecdsa_mp.h` `detach_private_scalar`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/ecdsa_mp.h::detach_private_scalar`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically input that triggers an inner parse/proof failure after partially filling output buffers after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point after refresh but before public-key export. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `include/cbmpc/api/ecdsa_mp.h` `detach_private_scalar` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `include/cbmpc/api/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `include/cbmpc/api/ecdsa_mp.h` `detach_private_scalar` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
