# Q3128: EdDSA refresh old-new key mix in eddsa.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point after a failed attempt is retried with fresh inputs, reach `include-internal/cbmpc/internal/protocol/eddsa.h` `sign_batch`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/eddsa.h::sign_batch`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically refresh transcript that mixes old public shares with new private shares after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point after a failed attempt is retried with fresh inputs. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `include-internal/cbmpc/internal/protocol/eddsa.h` `sign_batch` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `include-internal/cbmpc/internal/protocol/eddsa.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `include-internal/cbmpc/internal/protocol/eddsa.h` `sign_batch` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
