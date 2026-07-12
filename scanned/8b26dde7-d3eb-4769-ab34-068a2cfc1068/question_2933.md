# Q2933: MPC transport public key extraction mismatch in mpc_job.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after successful DKG and before signing, reach `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/mpc_job.cpp::send_to_parties`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically key_blob whose public-key extraction path and signing path parse different fields after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after successful DKG and before signing. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/protocol/mpc_job.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `src/cbmpc/protocol/mpc_job.cpp` `send_to_parties` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
