# Q3863: ECDSA-2PC converter trailing-data trust in ecdsa_2p.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript after refresh but before public-key export, reach `src/cbmpc/protocol/ecdsa_2p.cpp` `prove`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_2p.cpp::prove`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically serialized object with a valid prefix plus trailing attacker fields after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript after refresh but before public-key export. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/protocol/ecdsa_2p.cpp` `prove` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `src/cbmpc/protocol/ecdsa_2p.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/protocol/ecdsa_2p.cpp` `prove` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
