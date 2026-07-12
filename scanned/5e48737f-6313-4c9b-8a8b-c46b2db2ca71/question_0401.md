# Q401: ZK proof proof flag trust gap in zk_pedersen.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after a failed attempt is retried with fresh inputs, reach `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup`, and use proof messages that imply prerequisite proof flags without the prerequisite transcript to bypass the requirement that every prerequisite ZK statement is established before dependent flags are trusted, causing an invalid cryptographic statement feeds an accepted protocol output and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_pedersen.cpp::check_safe_prime_subgroup`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically proof messages that imply prerequisite proof flags without the prerequisite transcript after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after a failed attempt is retried with fresh inputs. The malicious side supplies proof messages that imply prerequisite proof flags without the prerequisite transcript. Investigate whether `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup` assumes every prerequisite ZK statement is established before dependent flags are trusted was already enforced and therefore lets an invalid cryptographic statement feeds an accepted protocol output.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/zk/zk_pedersen.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate proof messages that imply prerequisite proof flags without the prerequisite transcript; assert rejection before `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
