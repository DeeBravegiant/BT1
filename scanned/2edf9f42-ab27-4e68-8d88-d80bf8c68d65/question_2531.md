# Q2531: MPC transport converter trailing-data trust in mpc_job.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during backup verification before recovery, reach `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/mpc_job.cpp::send_to_parties`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically serialized object with a valid prefix plus trailing attacker fields during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during backup verification before recovery. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/protocol/mpc_job.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
