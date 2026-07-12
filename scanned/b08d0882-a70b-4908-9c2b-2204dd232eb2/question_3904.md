# Q3904: MPC transport converter trailing-data trust in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` with keyset_blob, hardened_path, and malicious derivation transcript while one malicious peer deviates and one honest party is unmodified, reach `include/cbmpc/core/job.h` `receive_all`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::receive_all`
- Entrypoint: `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys via include/cbmpc/api/hd_keyset_ecdsa_2p.h`
- Attacker controls: keyset_blob, hardened_path, and malicious derivation transcript; specifically serialized object with a valid prefix plus trailing attacker fields while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` in `include/cbmpc/api/hd_keyset_ecdsa_2p.h` with keyset_blob, hardened_path, and malicious derivation transcript while one malicious peer deviates and one honest party is unmodified. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include/cbmpc/core/job.h` `receive_all` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include/cbmpc/core/job.h` `receive_all` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
