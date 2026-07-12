# Q3561: MPC transport public key extraction mismatch in job_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages when labels or sids are reused across supported flows, reach `src/cbmpc/api/job_util.h` `validate_job_2p`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/job_util.h::validate_job_2p`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically key_blob whose public-key extraction path and signing path parse different fields when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages when labels or sids are reused across supported flows. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `src/cbmpc/api/job_util.h` `validate_job_2p` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/api/job_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `src/cbmpc/api/job_util.h` `validate_job_2p` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
