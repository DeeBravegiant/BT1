# Q2088: MPC transport session replay in job_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages after refresh but before public-key export, reach `src/cbmpc/api/job_util.h` `validate_job_mp`, and use a reused sid, aux value, or transcript fragment from a concurrent execution to bypass the requirement that session and aux values are domain-separated by protocol, round, party set, curve, and subproof, causing replayed commitments, proofs, or messages are accepted in another execution and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/job_util.h::validate_job_mp`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically a reused sid, aux value, or transcript fragment from a concurrent execution after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages after refresh but before public-key export. The malicious side supplies a reused sid, aux value, or transcript fragment from a concurrent execution. Investigate whether `src/cbmpc/api/job_util.h` `validate_job_mp` assumes session and aux values are domain-separated by protocol, round, party set, curve, and subproof was already enforced and therefore lets replayed commitments, proofs, or messages are accepted in another execution.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/api/job_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a reused sid, aux value, or transcript fragment from a concurrent execution; assert rejection before `src/cbmpc/api/job_util.h` `validate_job_mp` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
