# Q1970: cb-mpc protocol non-canonical signature or key encoding in base_bn256.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript while two sessions run concurrently, reach `src/cbmpc/crypto/base_bn256.cpp` `base_bn256 module`, and use DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings to bypass the requirement that signature and public-key encodings are canonical before comparison/export, causing modules disagree about the same key or signature and accept attacker binding and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_bn256.cpp::base_bn256 module`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript while two sessions run concurrently. The malicious side supplies DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings. Investigate whether `src/cbmpc/crypto/base_bn256.cpp` `base_bn256 module` assumes signature and public-key encodings are canonical before comparison/export was already enforced and therefore lets modules disagree about the same key or signature and accept attacker binding.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/crypto/base_bn256.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings; assert rejection before `src/cbmpc/crypto/base_bn256.cpp` `base_bn256 module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
