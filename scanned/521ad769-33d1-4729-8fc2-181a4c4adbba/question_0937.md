# Q937: ZK proof public share substitution in zk_pedersen.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label after a failed attempt is retried with fresh inputs, reach `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup`, and use public_share_compressed from one blob paired with scalar from another blob to bypass the requirement that attach APIs bind scalar to blob role, curve, public share, public key, and party, causing attacker restores scalar into a blob that should not be sign-capable and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_pedersen.cpp::check_safe_prime_subgroup`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically public_share_compressed from one blob paired with scalar from another blob after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label after a failed attempt is retried with fresh inputs. The malicious side supplies public_share_compressed from one blob paired with scalar from another blob. Investigate whether `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup` assumes attach APIs bind scalar to blob role, curve, public share, public key, and party was already enforced and therefore lets attacker restores scalar into a blob that should not be sign-capable.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/zk/zk_pedersen.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public_share_compressed from one blob paired with scalar from another blob; assert rejection before `src/cbmpc/zk/zk_pedersen.cpp` `check_safe_prime_subgroup` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
