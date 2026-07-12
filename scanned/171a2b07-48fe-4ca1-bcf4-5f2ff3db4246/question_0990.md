# Q990: MPC transport converter trailing-data trust in mpc_job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages during the first accepted protocol run, reach `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_impl`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/mpc_job.h::send_impl`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically serialized object with a valid prefix plus trailing attacker fields during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages during the first accepted protocol run. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_impl` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `include-internal/cbmpc/internal/protocol/mpc_job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_impl` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
