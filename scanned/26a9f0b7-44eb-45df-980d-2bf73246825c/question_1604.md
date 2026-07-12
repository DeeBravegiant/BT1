# Q1604: ZK proof public key extraction mismatch in zk_ec.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::attach_private_scalar` with public_key_blob, variable-length private_scalar, and public_share_compressed after a failed attempt is retried with fresh inputs, reach `src/cbmpc/zk/zk_ec.cpp` `verify`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_ec.cpp::verify`
- Entrypoint: `coinbase::api::ecdsa_2p::attach_private_scalar via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: public_key_blob, variable-length private_scalar, and public_share_compressed; specifically key_blob whose public-key extraction path and signing path parse different fields after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::attach_private_scalar` in `include/cbmpc/api/ecdsa_2p.h` with public_key_blob, variable-length private_scalar, and public_share_compressed after a failed attempt is retried with fresh inputs. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `src/cbmpc/zk/zk_ec.cpp` `verify` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::attach_private_scalar` through `src/cbmpc/zk/zk_ec.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `src/cbmpc/zk/zk_ec.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
