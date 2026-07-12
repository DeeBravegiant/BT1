# Q1895: MPC transport committed broadcast equivocation in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages during the first accepted protocol run, reach `include/cbmpc/core/job.h` `receive_all`, and use different packed broadcast payloads sent to different parties in the same round to bypass the requirement that committed broadcast binds payload to sender, recipient set, sid, and round, causing honest parties derive divergent state while one output is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::receive_all`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically different packed broadcast payloads sent to different parties in the same round during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages during the first accepted protocol run. The malicious side supplies different packed broadcast payloads sent to different parties in the same round. Investigate whether `include/cbmpc/core/job.h` `receive_all` assumes committed broadcast binds payload to sender, recipient set, sid, and round was already enforced and therefore lets honest parties derive divergent state while one output is accepted.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate different packed broadcast payloads sent to different parties in the same round; assert rejection before `include/cbmpc/core/job.h` `receive_all` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
