# Q1326: MPC transport scalar width confusion in mpc_job.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after refresh but before public-key export, reach `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties`, and use zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding to bypass the requirement that scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction, causing a substituted scalar becomes usable key material and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/mpc_job.cpp::send_to_parties`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after refresh but before public-key export. The malicious side supplies zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding. Investigate whether `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` assumes scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction was already enforced and therefore lets a substituted scalar becomes usable key material.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/protocol/mpc_job.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding; assert rejection before `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
