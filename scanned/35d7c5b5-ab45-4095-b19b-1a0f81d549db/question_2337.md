# Q2337: MPC transport Fischlin challenge domain gap in sid.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing, reach `include-internal/cbmpc/internal/protocol/sid.h` `generate_sid_dynamic_2p`, and use two proof statements with colliding serialized challenge inputs to bypass the requirement that Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id, causing proof for one statement verifies as another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/sid.h::generate_sid_dynamic_2p`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically two proof statements with colliding serialized challenge inputs after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing. The malicious side supplies two proof statements with colliding serialized challenge inputs. Investigate whether `include-internal/cbmpc/internal/protocol/sid.h` `generate_sid_dynamic_2p` assumes Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id was already enforced and therefore lets proof for one statement verifies as another.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `include-internal/cbmpc/internal/protocol/sid.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two proof statements with colliding serialized challenge inputs; assert rejection before `include-internal/cbmpc/internal/protocol/sid.h` `generate_sid_dynamic_2p` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
