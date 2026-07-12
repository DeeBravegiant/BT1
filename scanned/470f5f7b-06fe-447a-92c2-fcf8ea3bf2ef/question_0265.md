# Q265: ZK proof session replay in zk_elgamal_com.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript after successful DKG and before signing, reach `src/cbmpc/zk/zk_elgamal_com.cpp` `verify`, and use a reused sid, aux value, or transcript fragment from a concurrent execution to bypass the requirement that session and aux values are domain-separated by protocol, round, party set, curve, and subproof, causing replayed commitments, proofs, or messages are accepted in another execution and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_elgamal_com.cpp::verify`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically a reused sid, aux value, or transcript fragment from a concurrent execution after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript after successful DKG and before signing. The malicious side supplies a reused sid, aux value, or transcript fragment from a concurrent execution. Investigate whether `src/cbmpc/zk/zk_elgamal_com.cpp` `verify` assumes session and aux values are domain-separated by protocol, round, party set, curve, and subproof was already enforced and therefore lets replayed commitments, proofs, or messages are accepted in another execution.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `src/cbmpc/zk/zk_elgamal_com.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a reused sid, aux value, or transcript fragment from a concurrent execution; assert rejection before `src/cbmpc/zk/zk_elgamal_com.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
