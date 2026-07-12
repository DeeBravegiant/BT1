# Q1001: ZK proof Paillier modulus validation gap in small_primes.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript when parties disagree on recipient or quorum ordering, reach `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes`, and use Paillier key or ciphertext with malformed modulus/ciphertext structure to bypass the requirement that Paillier modulus and ciphertext validity are established before dependent proofs, causing invalid homomorphic value contributes to accepted ECDSA transcript and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/zk/small_primes.h::check_integer_with_small_primes`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically Paillier key or ciphertext with malformed modulus/ciphertext structure when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript when parties disagree on recipient or quorum ordering. The malicious side supplies Paillier key or ciphertext with malformed modulus/ciphertext structure. Investigate whether `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` assumes Paillier modulus and ciphertext validity are established before dependent proofs was already enforced and therefore lets invalid homomorphic value contributes to accepted ECDSA transcript.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `include-internal/cbmpc/internal/zk/small_primes.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate Paillier key or ciphertext with malformed modulus/ciphertext structure; assert rejection before `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
