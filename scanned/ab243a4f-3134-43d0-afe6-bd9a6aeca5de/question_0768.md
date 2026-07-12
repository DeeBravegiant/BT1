# Q768: ECC validation fixed-buffer exactness gap in base_ecc_secp256k1.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::schnorr_2p::sign` with key_blob, 32-byte digest, and malicious peer transcript during backup verification before recovery, reach `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify`, and use buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary to bypass the requirement that fixed-size buffers reject non-exact lengths without truncation or padding, causing modules use different bytes for the same scalar, sid, label, or digest and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_ecc_secp256k1.cpp::ossl_ecdsa_verify`
- Entrypoint: `coinbase::api::schnorr_2p::sign via include/cbmpc/api/schnorr_2p.h`
- Attacker controls: key_blob, 32-byte digest, and malicious peer transcript; specifically buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::schnorr_2p::sign` in `include/cbmpc/api/schnorr_2p.h` with key_blob, 32-byte digest, and malicious peer transcript during backup verification before recovery. The malicious side supplies buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary. Investigate whether `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify` assumes fixed-size buffers reject non-exact lengths without truncation or padding was already enforced and therefore lets modules use different bytes for the same scalar, sid, label, or digest.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::schnorr_2p::sign` through `src/cbmpc/crypto/base_ecc_secp256k1.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Medium public-API reachable invariant break with invalid cryptographic output or unsafe accepted state.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate buf128/buf256-sized value with non-exact length, implicit padding, or truncation boundary; assert rejection before `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `ossl_ecdsa_verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
