# Q3974: serialization/core message digest semantic confusion in buf256.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::attach_private_scalar` with public_key_blob, variable-length private_scalar, and public_share_compressed after a failed attempt is retried with fresh inputs, reach `src/cbmpc/core/buf256.cpp` `buf256 module`, and use signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity to bypass the requirement that ECDSA/Schnorr enforce exact digest semantics while EdDSA binds raw message, causing valid signature is produced over unintended message bytes and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/core/buf256.cpp::buf256 module`
- Entrypoint: `coinbase::api::ecdsa_2p::attach_private_scalar via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: public_key_blob, variable-length private_scalar, and public_share_compressed; specifically signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::attach_private_scalar` in `include/cbmpc/api/ecdsa_2p.h` with public_key_blob, variable-length private_scalar, and public_share_compressed after a failed attempt is retried with fresh inputs. The malicious side supplies signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity. Investigate whether `src/cbmpc/core/buf256.cpp` `buf256 module` assumes ECDSA/Schnorr enforce exact digest semantics while EdDSA binds raw message was already enforced and therefore lets valid signature is produced over unintended message bytes.
- Invariant to test: The serialization/core path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::attach_private_scalar` through `src/cbmpc/core/buf256.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical valid signing result without required honest two-party or threshold participation.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity; assert rejection before `src/cbmpc/core/buf256.cpp` `buf256 module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
