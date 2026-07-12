# Q3689: ECDSA-MP receiver-only output confusion in ecdsa_mp.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages when public extraction is compared with signing output, reach `src/cbmpc/api/ecdsa_mp.cpp` `refresh_ac`, and use sig_receiver values that differ across parties or hit boundary indices to bypass the requirement that all parties agree on receiver identity and only the intended receiver treats output as final, causing a signature is produced or accepted despite inconsistent receiver semantics and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa_mp.cpp::refresh_ac`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically sig_receiver values that differ across parties or hit boundary indices when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages when public extraction is compared with signing output. The malicious side supplies sig_receiver values that differ across parties or hit boundary indices. Investigate whether `src/cbmpc/api/ecdsa_mp.cpp` `refresh_ac` assumes all parties agree on receiver identity and only the intended receiver treats output as final was already enforced and therefore lets a signature is produced or accepted despite inconsistent receiver semantics.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/api/ecdsa_mp.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical valid signing result without required honest two-party or threshold participation.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate sig_receiver values that differ across parties or hit boundary indices; assert rejection before `src/cbmpc/api/ecdsa_mp.cpp` `refresh_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
