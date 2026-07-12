# Q3545: ZK proof converter trailing-data trust in small_primes.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_batch` with ek, ciphertext, Q vector, and label after a failed attempt is retried with fresh inputs, reach `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/zk/small_primes.h::check_integer_with_small_primes`
- Entrypoint: `coinbase::api::pve::verify_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: ek, ciphertext, Q vector, and label; specifically serialized object with a valid prefix plus trailing attacker fields after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with ek, ciphertext, Q vector, and label after a failed attempt is retried with fresh inputs. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_batch` through `include-internal/cbmpc/internal/zk/small_primes.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include-internal/cbmpc/internal/zk/small_primes.h` `check_integer_with_small_primes` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
