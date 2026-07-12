# Q3220: EdDSA refresh old-new key mix in eddsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_2p::sign` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently, reach `include/cbmpc/api/eddsa_mp.h` `sign_additive`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/eddsa_mp.h::sign_additive`
- Entrypoint: `coinbase::api::eddsa_2p::sign via include/cbmpc/api/eddsa_2p.h`
- Attacker controls: key_blob, raw message, and malicious two-party transcript; specifically refresh transcript that mixes old public shares with new private shares while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::eddsa_2p::sign` in `include/cbmpc/api/eddsa_2p.h` with key_blob, raw message, and malicious two-party transcript while two sessions run concurrently. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `include/cbmpc/api/eddsa_mp.h` `sign_additive` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_2p::sign` through `include/cbmpc/api/eddsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `include/cbmpc/api/eddsa_mp.h` `sign_additive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
