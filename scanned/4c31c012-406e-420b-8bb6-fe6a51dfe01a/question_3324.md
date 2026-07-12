# Q3324: access-structure curve binding drift in secret_sharing.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::schnorr_mp::sign_ac` with ac key blob, access_structure, digest, receiver, and peer messages when parties disagree on recipient or quorum ordering, reach `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_recursive`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/secret_sharing.cpp::reconstruct_recursive`
- Entrypoint: `coinbase::api::schnorr_mp::sign_ac via include/cbmpc/api/schnorr_mp.h`
- Attacker controls: ac key blob, access_structure, digest, receiver, and peer messages; specifically a curve_id paired with points or scalars from another supported curve when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::schnorr_mp::sign_ac` in `include/cbmpc/api/schnorr_mp.h` with ac key blob, access_structure, digest, receiver, and peer messages when parties disagree on recipient or quorum ordering. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_recursive` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::schnorr_mp::sign_ac` through `src/cbmpc/crypto/secret_sharing.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/crypto/secret_sharing.cpp` `reconstruct_recursive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
