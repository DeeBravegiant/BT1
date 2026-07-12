# Q1612: EdDSA Fischlin challenge domain gap in eddsa_2p.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently, reach `include/cbmpc/api/eddsa_2p.h` `detach_private_scalar`, and use two proof statements with colliding serialized challenge inputs to bypass the requirement that Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id, causing proof for one statement verifies as another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/eddsa_2p.h::detach_private_scalar`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically two proof statements with colliding serialized challenge inputs while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently. The malicious side supplies two proof statements with colliding serialized challenge inputs. Investigate whether `include/cbmpc/api/eddsa_2p.h` `detach_private_scalar` assumes Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id was already enforced and therefore lets proof for one statement verifies as another.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `include/cbmpc/api/eddsa_2p.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two proof statements with colliding serialized challenge inputs; assert rejection before `include/cbmpc/api/eddsa_2p.h` `detach_private_scalar` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
