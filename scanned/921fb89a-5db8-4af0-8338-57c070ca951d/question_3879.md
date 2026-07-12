# Q3879: ZK proof curve binding drift in small_primes.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::refresh` with key_blob and malicious refresh transcript after a failed attempt is retried with fresh inputs, reach `src/cbmpc/zk/small_primes.cpp` `small_primes module`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/small_primes.cpp::small_primes module`
- Entrypoint: `coinbase::api::ecdsa_2p::refresh via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob and malicious refresh transcript; specifically a curve_id paired with points or scalars from another supported curve after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::refresh` in `include/cbmpc/api/ecdsa_2p.h` with key_blob and malicious refresh transcript after a failed attempt is retried with fresh inputs. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/zk/small_primes.cpp` `small_primes module` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::refresh` through `src/cbmpc/zk/small_primes.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/zk/small_primes.cpp` `small_primes module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
