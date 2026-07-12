# Q2785: ZK proof ElGamal commitment reuse in elgamal.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages while one malicious peer deviates and one honest party is unmodified, reach `src/cbmpc/crypto/elgamal.cpp` `elgamal module`, and use ElGamal commitment values replayed across sessions or statement contexts to bypass the requirement that commitments are rerandomized or transcript-bound before proofs/signing equations, causing commitment relation leaks share information or validates a false statement and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/elgamal.cpp::elgamal module`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically ElGamal commitment values replayed across sessions or statement contexts while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages while one malicious peer deviates and one honest party is unmodified. The malicious side supplies ElGamal commitment values replayed across sessions or statement contexts. Investigate whether `src/cbmpc/crypto/elgamal.cpp` `elgamal module` assumes commitments are rerandomized or transcript-bound before proofs/signing equations was already enforced and therefore lets commitment relation leaks share information or validates a false statement.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/crypto/elgamal.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate ElGamal commitment values replayed across sessions or statement contexts; assert rejection before `src/cbmpc/crypto/elgamal.cpp` `elgamal module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
