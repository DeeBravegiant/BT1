# Q3468: MPC transport committed broadcast equivocation in mpc_job.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages during the first accepted protocol run, reach `src/cbmpc/protocol/mpc_job.cpp` `receive_many_impl`, and use different packed broadcast payloads sent to different parties in the same round to bypass the requirement that committed broadcast binds payload to sender, recipient set, sid, and round, causing honest parties derive divergent state while one output is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/mpc_job.cpp::receive_many_impl`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically different packed broadcast payloads sent to different parties in the same round during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages during the first accepted protocol run. The malicious side supplies different packed broadcast payloads sent to different parties in the same round. Investigate whether `src/cbmpc/protocol/mpc_job.cpp` `receive_many_impl` assumes committed broadcast binds payload to sender, recipient set, sid, and round was already enforced and therefore lets honest parties derive divergent state while one output is accepted.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `src/cbmpc/protocol/mpc_job.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate different packed broadcast payloads sent to different parties in the same round; assert rejection before `src/cbmpc/protocol/mpc_job.cpp` `receive_many_impl` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
