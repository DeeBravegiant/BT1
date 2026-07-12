# Q2424: TDH2 partial output replay in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label during threshold combine with a minimal quorum, reach `include/cbmpc/api/tdh2.h` `partial_decrypt`, and use partial_decryption or quorum share replayed after failed attempt with different attempt_index or label to bypass the requirement that attempt index, label, ciphertext, and failure state are bound into reconstruction, causing failed attempt material is replayed to recover wrong plaintext/scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::partial_decrypt`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically partial_decryption or quorum share replayed after failed attempt with different attempt_index or label during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label during threshold combine with a minimal quorum. The malicious side supplies partial_decryption or quorum share replayed after failed attempt with different attempt_index or label. Investigate whether `include/cbmpc/api/tdh2.h` `partial_decrypt` assumes attempt index, label, ciphertext, and failure state are bound into reconstruction was already enforced and therefore lets failed attempt material is replayed to recover wrong plaintext/scalar.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate partial_decryption or quorum share replayed after failed attempt with different attempt_index or label; assert rejection before `include/cbmpc/api/tdh2.h` `partial_decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
