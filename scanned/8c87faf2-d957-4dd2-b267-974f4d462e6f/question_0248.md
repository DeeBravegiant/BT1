# Q248: ECDSA-MP curve binding drift in ecdsa_mp.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages while one malicious peer deviates and one honest party is unmodified, reach `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_mp.cpp::refresh`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically a curve_id paired with points or scalars from another supported curve while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages while one malicious peer deviates and one honest party is unmodified. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `src/cbmpc/protocol/ecdsa_mp.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
