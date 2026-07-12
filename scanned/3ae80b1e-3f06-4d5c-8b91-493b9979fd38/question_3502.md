# Q3502: MPC transport refresh old-new key mix in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages when parties disagree on recipient or quorum ordering, reach `include/cbmpc/core/job.h` `receive_all`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::receive_all`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically refresh transcript that mixes old public shares with new private shares when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages when parties disagree on recipient or quorum ordering. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `include/cbmpc/core/job.h` `receive_all` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `include/cbmpc/core/job.h` `receive_all` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
