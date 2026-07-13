Looking at the actual code in `src/cbmpc/crypto/base_mod.cpp` and `include-internal/cbmpc/internal/crypto/base_mod.h`, I can trace the exact call sequence and evaluate each claim.

**Claim 1: An attacker-controlled `bn_t` with `BN_FLG_FIXED_TOP` and extra zero high limbs reaches `_mul`.**

`_mul` calls `check(a)` before touching `aa.top`: [1](#0-0) 

`check` calls `is_in_range(a)` which calls `a < m`, which calls OpenSSL's `BN_cmp`. `BN_cmp` compares `top` fields first — if `a->top > m->top`, it returns 1 (a > m) regardless of whether the extra limbs are zero.

### Citations

**File:** src/cbmpc/crypto/base_mod.cpp (L163-167)
```cpp
  check(a);
  check(b);

  const BIGNUM& aa = *(const BIGNUM*)a;
  const BIGNUM& bb = *(const BIGNUM*)b;
```
