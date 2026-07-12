# Q1013: EdDSA non-canonical signature or key encoding in eddsa_mp.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages when parties disagree on recipient or quorum ordering, reach `src/cbmpc/api/eddsa_mp.cpp` `dkg_ac`, and use DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings to bypass the requirement that signature and public-key encodings are canonical before comparison/export, causing modules disagree about the same key or signature and accept attacker binding and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/eddsa_mp.cpp::dkg_ac`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages when parties disagree on recipient or quorum ordering. The malicious side supplies DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings. Investigate whether `src/cbmpc/api/eddsa_mp.cpp` `dkg_ac` assumes signature and public-key encodings are canonical before comparison/export was already enforced and therefore lets modules disagree about the same key or signature and accept attacker binding.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `src/cbmpc/api/eddsa_mp.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings; assert rejection before `src/cbmpc/api/eddsa_mp.cpp` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
