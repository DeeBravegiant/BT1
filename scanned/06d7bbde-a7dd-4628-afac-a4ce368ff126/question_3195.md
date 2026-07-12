# Q3195: ECDSA-MP party-name aliasing in ecdsa_mp.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing, reach `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh_ac`, and use duplicate, reordered, empty, or colliding party_names and quorum_party_names to bypass the requirement that party names map one-to-one to stable pids and access-structure leaves, causing a below-threshold set is treated as a valid quorum or share owner and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_mp.cpp::refresh_ac`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically duplicate, reordered, empty, or colliding party_names and quorum_party_names after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing. The malicious side supplies duplicate, reordered, empty, or colliding party_names and quorum_party_names. Investigate whether `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh_ac` assumes party names map one-to-one to stable pids and access-structure leaves was already enforced and therefore lets a below-threshold set is treated as a valid quorum or share owner.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `src/cbmpc/protocol/ecdsa_mp.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate duplicate, reordered, empty, or colliding party_names and quorum_party_names; assert rejection before `src/cbmpc/protocol/ecdsa_mp.cpp` `refresh_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
