# Q1269: ZK proof Fischlin challenge domain gap in small_primes.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript during threshold combine with a minimal quorum, reach `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes`, and use two proof statements with colliding serialized challenge inputs to bypass the requirement that Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id, causing proof for one statement verifies as another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/zk/small_primes.h::check_integer_with_small_primes`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically two proof statements with colliding serialized challenge inputs during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript during threshold combine with a minimal quorum. The malicious side supplies two proof statements with colliding serialized challenge inputs. Investigate whether `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` assumes Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id was already enforced and therefore lets proof for one statement verifies as another.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `include-internal/cbmpc/internal/zk/small_primes.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two proof statements with colliding serialized challenge inputs; assert rejection before `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
