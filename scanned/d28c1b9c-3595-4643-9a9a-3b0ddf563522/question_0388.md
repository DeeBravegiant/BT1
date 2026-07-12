# Q388: MPC transport error-state confusion in mpc_job.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point during threshold combine with a minimal quorum, reach `src/cbmpc/protocol/mpc_job.cpp` `receive_from_parties`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/mpc_job.cpp::receive_from_parties`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically input that triggers an inner parse/proof failure after partially filling output buffers during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point during threshold combine with a minimal quorum. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `src/cbmpc/protocol/mpc_job.cpp` `receive_from_parties` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `src/cbmpc/protocol/mpc_job.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `src/cbmpc/protocol/mpc_job.cpp` `receive_from_parties` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
