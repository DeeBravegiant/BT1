# Q1947: ECDSA-2PC ElGamal commitment reuse in ecdsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery, reach `src/cbmpc/api/ecdsa2pc.cpp` `dkg`, and use ElGamal commitment values replayed across sessions or statement contexts to bypass the requirement that commitments are rerandomized or transcript-bound before proofs/signing equations, causing commitment relation leaks share information or validates a false statement and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa2pc.cpp::dkg`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically ElGamal commitment values replayed across sessions or statement contexts during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery. The malicious side supplies ElGamal commitment values replayed across sessions or statement contexts. Investigate whether `src/cbmpc/api/ecdsa2pc.cpp` `dkg` assumes commitments are rerandomized or transcript-bound before proofs/signing equations was already enforced and therefore lets commitment relation leaks share information or validates a false statement.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/api/ecdsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate ElGamal commitment values replayed across sessions or statement contexts; assert rejection before `src/cbmpc/api/ecdsa2pc.cpp` `dkg` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
