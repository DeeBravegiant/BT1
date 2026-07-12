# Q2490: MPC transport error-state confusion in job_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::decrypt_batch` with dk, ek, ciphertext, and label when parties disagree on recipient or quorum ordering, reach `src/cbmpc/api/job_util.h` `validate_job_2p`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/job_util.h::validate_job_2p`
- Entrypoint: `coinbase::api::pve::decrypt_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: dk, ek, ciphertext, and label; specifically input that triggers an inner parse/proof failure after partially filling output buffers when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::pve::decrypt_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with dk, ek, ciphertext, and label when parties disagree on recipient or quorum ordering. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `src/cbmpc/api/job_util.h` `validate_job_2p` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::decrypt_batch` through `src/cbmpc/api/job_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `src/cbmpc/api/job_util.h` `validate_job_2p` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
