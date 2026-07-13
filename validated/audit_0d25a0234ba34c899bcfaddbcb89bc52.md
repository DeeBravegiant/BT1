Let me trace the full attack path through the code before rendering a verdict.

**`is_valid_modulus` check** (line 19): [1](#0-0) 

Only checks `m > 1`, `bits <= MAX_MODULUS_BITS`, and `m.is_odd()`. A 2048-bit prime satisfies all three. No compositeness/primality check.

**`valid_paillier_t::verify` core loop** (lines 39–44): [2](#0-1) 

The check is `sigma[i].pow_mod(N, N) != rho`. For prime N, Fermat's little theorem gives `x^N ≡ x (mod N)` for all x with `gcd(x,N)=1`. So if the attacker computes `sigma[i] = rho[i]^(N^{-1} mod (N-1)) mod N`, then `sigma[i]^N mod N = rho[i]^(N·N^{-1} mod (N-1)) mod N = rho[i]^1 mod N = rho[i]`. The check passes trivially.

**`check_integer_with_small_primes`** (lines 11–18): [3](#0-2) 

Only checks that N has no small prime factors up to `param::alpha`. A large prime N has no small factors — it IS a prime — so this check passes.

**`coprime` check** (line 45): [4](#0-3) 

For prime N, every non-zero element of Z_N is coprime to N. The rho values are random in [0,N), so this passes with overwhelming probability.

**Decryption with prime N**: Paillier decryption with phi(N) = N-1 still works correctly. `c^(N-1) mod N^2 = (1+mN)^(N-1) * r^(N(N-1)) mod N^2`. Since phi(N^2) = N(N-1) for prime N, `r^(N(N-1)) ≡ 1 mod N^2`. Then `(1+mN)^(N-1) ≡ 1 + (N-1)mN mod N^2`, so `L(·) = (N-1)m mod N = -m mod N`, and multiplying by `(N-1)^{-1} mod N = -1 mod N` recovers `m`. The attacker can decrypt correctly.

---

### Title
ZK-Valid-Paillier Proof Accepts Prime Modulus, Enabling Key-Share Recovery in ECDSA-2PC — (`src/cbmpc/zk/zk_paillier.cpp`, `src/cbmpc/crypto/base_mod.cpp`)

### Summary
`is_valid_modulus` has no primality/compositeness check. A Byzantine participant can submit a prime N as their Paillier public key, craft a matching `valid_paillier_t` proof that passes `verify` via Fermat's little theorem, and then decrypt any ciphertext the honest party encrypts under that key — recovering the honest party's key share.

### Finding Description
`mod_t::is_valid_modulus` accepts any odd integer > 1 within the bit-size limit: [1](#0-0) 

`paillier_t::convert` and `paillier_t::create_pub` both rely solely on this check: [5](#0-4) 

`valid_paillier_t::verify` checks `sigma[i]^N mod N == rho[i]` and that N has no small prime factors: [6](#0-5) 

For a prime N, Fermat's little theorem makes the sigma check trivially satisfiable: the attacker computes `N_inv = N^{-1} mod (N-1)` (since phi(N) = N-1), then `sigma[i] = rho[i]^N_inv mod N`. The small-factors check passes because a large prime has no small factors. The coprime check passes because every non-zero element of Z_p is coprime to p.

The prover-side `prove` function: [7](#0-6) 

A Byzantine attacker does not need to call `prove` — they compute sigma directly offline using the deterministic rho derivation (same DRBG seed from N, session_id, aux).

### Impact Explanation
Once `paillier_valid_key = zk_flag::verified` is set, the honest party encrypts their ECDSA key share under the attacker's prime-N Paillier key. The attacker decrypts using phi(N) = N-1 (which they know since they chose N), recovering the honest party's private key share. This enables full private key reconstruction and signature forgery without the honest party's participation.

### Likelihood Explanation
The attacker is a single Byzantine participant — below-threshold collusion is not required. The attack requires only generating a 2048-bit prime (standard operation) and computing one modular inverse. No side channels, no memory access, no threshold collusion needed.

### Recommendation
Add a compositeness check in `is_valid_modulus` or in `valid_paillier_t::verify`. The standard approach is a Miller-Rabin test (e.g., via `BN_is_prime_fasttest_ex`) to confirm N is composite, or explicitly verify that N has at least two distinct large prime factors (e.g., by checking that N is not prime and not a prime power). The small-primes sieve alone is insufficient — it only rules out small factors, not primality itself.

### Proof of Concept
```
1. Generate a 2048-bit prime N (e.g., via BN_generate_prime_ex).
2. Compute phi_N = N - 1.
3. Compute N_inv = N^{-1} mod phi_N  (exists since gcd(N, N-1) = 1).
4. Derive rho[i] deterministically: same DRBG seed = hash(N, session_id, aux).
5. Compute sigma[i] = rho[i]^N_inv mod N for each i.
6. Serialize (N, sigma[]) as a valid_paillier_t blob and send to the verifier.
7. valid_paillier_t::verify returns SUCCESS.
8. Honest party encrypts key share m under N: c = (1 + mN) * r^N mod N^2.
9. Attacker decrypts: c^(N-1) mod N^2, apply L, multiply by (N-1)^{-1} mod N → recovers m.
10. Assert: decrypted value equals the honest party's key share.
```

### Citations

**File:** src/cbmpc/crypto/base_mod.cpp (L19-19)
```cpp
bool mod_t::is_valid_modulus(const bn_t& m) { return m > 1 && m.get_bits_count() <= MAX_MODULUS_BITS && m.is_odd(); }
```

**File:** src/cbmpc/zk/zk_paillier.cpp (L7-22)
```cpp
void valid_paillier_t::prove(const crypto::paillier_t& paillier, mem_t session_id, uint64_t aux) {
  cb_assert(paillier.has_private_key());
  const mod_t& N = paillier.get_N();
  const bn_t& phi_N = paillier.get_phi_N();

  bn_t N_inv = mod_t::N_inv_mod_phiN_2048(N, phi_N);

  static_assert(SEC_P_COM == 128, "security parameter changed, please update the code");
  buf128_t k = crypto::ro::hash_string(N, session_id, aux).bitlen128();
  crypto::drbg_aes_ctr_t drbg(k);

  for (int i = 0; i < param::t; i++) {
    bn_t rho = drbg.gen_bn(N);
    sigma[i] = rho.pow_mod(N_inv, N);
  }
}
```

**File:** src/cbmpc/zk/zk_paillier.cpp (L33-45)
```cpp
  if (paillier_no_small_factors == zk_flag::unverified) {
    if (rv = check_integer_with_small_primes(N, param::alpha)) return rv;
    paillier_no_small_factors = zk_flag::verified;
  }

  bn_t rho_prod = 1;
  for (int i = 0; i < param::t; i++) {
    bn_t rho = drbg.gen_bn(N);
    MODULO(N) rho_prod *= rho;
    if (sigma[i] <= 0) return coinbase::error(E_CRYPTO);
    if (sigma[i].pow_mod(N, N) != rho) return coinbase::error(E_CRYPTO);
  }
  if (!mod_t::coprime(rho_prod, N)) return coinbase::error(E_CRYPTO);
```

**File:** include-internal/cbmpc/internal/zk/small_primes.h (L11-18)
```text
static error_t check_integer_with_small_primes(const bn_t& prime, int alpha) {
  for (int i = 0; i < small_primes_count; i++) {
    int small_prime = small_primes[i];
    if (small_prime > alpha) break;
    if (mod_t::mod(prime, small_prime) == 0) return coinbase::error(E_CRYPTO);
  }
  return SUCCESS;
}
```

**File:** src/cbmpc/crypto/base_paillier.cpp (L148-154)
```cpp
error_t paillier_t::create_pub(const bn_t& theN) {
  if (!mod_t::is_valid_modulus(theN)) return E_BADARG;
  if (theN.get_bits_count() > bit_size) return E_BADARG;
  N = mod_t(theN, /* multiplicative_dense */ true);
  has_private = false;
  update_public();
  return SUCCESS;
```
