# Q1628: MPC transport party-name aliasing in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_ac` with ciphertext, expected Qs, access_structure, leaf keys, and label when public extraction is compared with signing output, reach `include/cbmpc/core/job.h` `send`, and use duplicate, reordered, empty, or colliding party_names and quorum_party_names to bypass the requirement that party names map one-to-one to stable pids and access-structure leaves, causing a below-threshold set is treated as a valid quorum or share owner and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::send`
- Entrypoint: `coinbase::api::pve::verify_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, expected Qs, access_structure, leaf keys, and label; specifically duplicate, reordered, empty, or colliding party_names and quorum_party_names when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, expected Qs, access_structure, leaf keys, and label when public extraction is compared with signing output. The malicious side supplies duplicate, reordered, empty, or colliding party_names and quorum_party_names. Investigate whether `include/cbmpc/core/job.h` `send` assumes party names map one-to-one to stable pids and access-structure leaves was already enforced and therefore lets a below-threshold set is treated as a valid quorum or share owner.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_ac` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate duplicate, reordered, empty, or colliding party_names and quorum_party_names; assert rejection before `include/cbmpc/core/job.h` `send` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
