# Q2195: MPC transport transport unpack mismatch in mpc_job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages after refresh but before public-key export, reach `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_message_all_to_one`, and use packed bundle with missing, extra, reordered, or type-confused submessages to bypass the requirement that mpc_job unpacking preserves count, sender, recipient, and type order, causing protocol verifies one submessage but computes with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/mpc_job.h::send_message_all_to_one`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically packed bundle with missing, extra, reordered, or type-confused submessages after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages after refresh but before public-key export. The malicious side supplies packed bundle with missing, extra, reordered, or type-confused submessages. Investigate whether `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_message_all_to_one` assumes mpc_job unpacking preserves count, sender, recipient, and type order was already enforced and therefore lets protocol verifies one submessage but computes with another.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `include-internal/cbmpc/internal/protocol/mpc_job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate packed bundle with missing, extra, reordered, or type-confused submessages; assert rejection before `include-internal/cbmpc/internal/protocol/mpc_job.h` `send_message_all_to_one` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
