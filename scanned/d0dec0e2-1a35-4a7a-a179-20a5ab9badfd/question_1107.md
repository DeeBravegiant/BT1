# Q1107: ZK proof Fischlin challenge domain gap in elgamal.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows, reach `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve`, and use two proof statements with colliding serialized challenge inputs to bypass the requirement that Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id, causing proof for one statement verifies as another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/elgamal.h::check_curve`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically two proof statements with colliding serialized challenge inputs when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows. The malicious side supplies two proof statements with colliding serialized challenge inputs. Investigate whether `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` assumes Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id was already enforced and therefore lets proof for one statement verifies as another.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `include-internal/cbmpc/internal/crypto/elgamal.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two proof statements with colliding serialized challenge inputs; assert rejection before `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
