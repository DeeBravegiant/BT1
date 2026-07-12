# Q745: EdDSA receiver-only output confusion in eddsa_mp.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages during threshold combine with a minimal quorum, reach `src/cbmpc/api/eddsa_mp.cpp` `deserialize_ac_key_blob`, and use sig_receiver values that differ across parties or hit boundary indices to bypass the requirement that all parties agree on receiver identity and only the intended receiver treats output as final, causing a signature is produced or accepted despite inconsistent receiver semantics and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/eddsa_mp.cpp::deserialize_ac_key_blob`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically sig_receiver values that differ across parties or hit boundary indices during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages during threshold combine with a minimal quorum. The malicious side supplies sig_receiver values that differ across parties or hit boundary indices. Investigate whether `src/cbmpc/api/eddsa_mp.cpp` `deserialize_ac_key_blob` assumes all parties agree on receiver identity and only the intended receiver treats output as final was already enforced and therefore lets a signature is produced or accepted despite inconsistent receiver semantics.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/api/eddsa_mp.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical valid signing result without required honest two-party or threshold participation.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate sig_receiver values that differ across parties or hit boundary indices; assert rejection before `src/cbmpc/api/eddsa_mp.cpp` `deserialize_ac_key_blob` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
