# Q2827: access-structure converter trailing-data trust in access_structure.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently, reach `include/cbmpc/core/access_structure.h` `access_structure module`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/access_structure.h::access_structure module`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically serialized object with a valid prefix plus trailing attacker fields while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include/cbmpc/core/access_structure.h` `access_structure module` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `include/cbmpc/core/access_structure.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include/cbmpc/core/access_structure.h` `access_structure module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
