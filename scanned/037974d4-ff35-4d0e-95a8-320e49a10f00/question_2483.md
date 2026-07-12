# Q2483: ECDSA-2PC public key extraction mismatch in ecdsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery, reach `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa2pc.cpp::get_public_key_compressed`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically key_blob whose public-key extraction path and signing path parse different fields during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/api/ecdsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
