# Q951: TDH2 error-state confusion in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label when the same caller alternates valid and mutated blobs, reach `include/cbmpc/api/tdh2.h` `dkg_additive`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::dkg_additive`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically input that triggers an inner parse/proof failure after partially filling output buffers when the same caller alternates valid and mutated blobs
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label when the same caller alternates valid and mutated blobs. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `include/cbmpc/api/tdh2.h` `dkg_additive` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `include/cbmpc/api/tdh2.h` `dkg_additive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
