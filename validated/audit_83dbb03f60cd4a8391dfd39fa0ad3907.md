### Title
Byzantine Representative Equivocation on `c_all`/`h_all` Causes Split-Brain DKG Key State — (`src/cbmpc/protocol/ec_dkg.cpp`)

### Summary

In `key_share_mp_t::dkg_or_refresh_ac`, the consistency guard for `h_all` at line 377 compares each quorum party's broadcast `h_all` against the representative's `h_all`, but **never compares the locally computed `h_all._i` against the representative's value**. A Byzantine representative (the last quorum party by index) can equivocate on both `c_all` (round 1) and `h_all` (round 2) via `plain_broadcast`, causing different honest quorum parties to accept different commitment sets and therefore different public keys — a split-brain DKG output.

---

### Finding Description

**Relevant code path**: `dkg_ac` / `refresh_ac` → `dkg_or_refresh_ac` in `src/cbmpc/protocol/ec_dkg.cpp`.

**Step 1 — `c_all` broadcast (line 358)**

Each quorum party commits to its share contributions and broadcasts `c_all` via `plain_broadcast`:

```cpp
if (rv = job.plain_broadcast(c_all)) return ...;
```

`plain_broadcast` is implemented as `group_message(party_set_t::all(), party_set_t::all(), msgs...)`, which calls `send_to_parties` — a simple per-party unicast with no equivocation protection. A Byzantine representative (party index = `representative_quorum_pid_index`, the last quorum party) can send `c_A` to p0 and `c_B` to p1. [1](#0-0) [2](#0-1) 

**Step 2 — Local `h_all` computation (lines 360–367)**

Each party locally hashes the `c_all` values it received:

```cpp
h_all._i = crypto::sha256_t::hash(all_received_c_s, quorum_pids, sid);
```

Because p0 received `c_A` and p1 received `c_B` from the representative, they compute different values: `H_A` and `H_B` respectively. [3](#0-2) 

**Step 3 — `h_all` broadcast (line 369)**

All parties broadcast their locally computed `h_all` (along with `ac_pub_all`, `pi_r_all`, `rho_all`, `xij`) via another `plain_broadcast`. The representative again equivocates: it sends `H_B` to p0 and `H_A` to p1. [4](#0-3) 

**Step 4 — The broken guard (line 377)**

```cpp
if (h_all._j != h_all.received(representative_quorum_pid_index))
    return coinbase::error(E_CRYPTO, "h_all mismatch");
```

For p0 (i=0), iterating j over quorum parties:
- `j = p1`: checks `h_all.received(p1)` (= H_B) against `h_all.received(representative)` (= H_B sent by rep to p0). **Passes.**
- `j = representative`: checks `h_all.received(rep)` against itself. **Trivially passes.**

p0's own locally computed `H_A` is **never compared** to the representative's `H_B`. The same logic applies symmetrically for p1. [5](#0-4) 

**Step 5 — Non-quorum parties are protected; quorum parties are not (lines 400–403)**

```cpp
if (!quorum_party_set.has(i)) {
    if (h_all.received(representative_quorum_pid_index) != crypto::sha256_t::hash(cs, quorum_pids, sid))
        return coinbase::error(E_CRYPTO, "h_all mismatch");
}
```

Non-quorum parties correctly verify the representative's `h_all` against a locally recomputed hash. **Quorum parties skip this block entirely**, leaving the gap exploited above. [6](#0-5) 

**Step 6 — Split-brain key output (lines 405–458)**

The representative also sends different `ac_pub_all_A / rho_all_A / pi_r_all_A / xij_A` to p0 (consistent with `c_A`) and `ac_pub_all_B / rho_all_B / pi_r_all_B / xij_B` to p1 (consistent with `c_B`). Both parties pass commitment opening and ZK verification independently. They then compute different aggregate public keys:

- p0 derives `Q_A = Σ ac_pub_all_A[root]`
- p1 derives `Q_B = Σ ac_pub_all_B[root]`

The local self-consistency checks (`reconstructed_Q != Q`, `x_i * G != Qis[i]`) pass for each party independently, since each party's view is internally consistent. [7](#0-6) 

---

### Impact Explanation

Honest quorum parties accept a DKG output with different public keys. Any subsequent signing protocol will fail or produce invalid output, since the parties' key shares correspond to different public keys. Additionally, the Byzantine representative controls the representative's contribution to both `Q_A` and `Q_B`, and can arrange for one of the resulting keys to have a known private key (by choosing its secret share to cancel out honest contributions if it can predict them — though this requires additional conditions). At minimum, this is a confirmed **High** impact: a single Byzantine quorum party causes honest parties to accept different, irreconcilable cryptographic outputs from a DKG/refresh protocol.

---

### Likelihood Explanation

The representative role is deterministically assigned to the **last quorum party by index** (the loop at lines 284–291 always overwrites `representative_quorum_pid_index` with the last quorum `j`). Any participant who can arrange to be the last quorum party — or who is assigned that role — can execute this attack. `plain_broadcast` provides no equivocation protection. The attack requires only two rounds of crafted messages and no cryptographic breaks. [8](#0-7) 

---

### Recommendation

For quorum parties, add the same local recomputation check that non-quorum parties already perform. After building `cs` (line 389), quorum parties should verify:

```cpp
if (h_all.received(representative_quorum_pid_index) != h_all._i)
    return coinbase::error(E_CRYPTO, "h_all mismatch with local computation");
```

This ensures that the representative's broadcast `h_all` is consistent with the locally observed `c_all` values, closing the equivocation window. Alternatively, replace `plain_broadcast(c_all)` with a committed broadcast (as already used elsewhere in the codebase) to prevent equivocation at the source.

---

### Proof of Concept

**3-party simulation** (p0, p1, p2; all quorum; p2 = representative = Byzantine):

1. p2 generates two distinct commitment pairs `(c_A, rho_A, Rs_A, pi_A, shares_A)` and `(c_B, rho_B, Rs_B, pi_B, shares_B)` for two different random secrets.
2. **Round 1**: p2 sends `c_A` to p0, `c_B` to p1. p0 and p1 send their honest `c_all` to everyone.
3. p0 computes `H_A = SHA256({c_A, c_p0, c_p1}, quorum_pids, sid)`. p1 computes `H_B = SHA256({c_B, c_p0, c_p1}, quorum_pids, sid)`.
4. **Round 2**: p0 broadcasts `H_A`, p1 broadcasts `H_B`. p2 sends `H_B` to p0 and `H_A` to p1 (swapped), along with `(ac_pub_A, pi_A, rho_A, xij_A)` to p0 and `(ac_pub_B, pi_B, rho_B, xij_B)` to p1.
5. **p0's check** (line 377, j=p1): `H_B == H_B` ✓. j=p2: trivially ✓. p0's own `H_A` is never checked.
6. **p1's check** (line 377, j=p0): `H_A == H_A` ✓. j=p2: trivially ✓. p1's own `H_B` is never checked.
7. Both parties open their respective commitments successfully and derive `Q_A ≠ Q_B`.
8. Assert: `p0.key.Q != p1.key.Q` — split-brain confirmed.

### Citations

**File:** src/cbmpc/protocol/mpc_job.cpp (L5-12)
```cpp
error_t job_mp_t::send_to_parties(party_set_t set, const std::vector<buf_t>& in) {
  error_t rv = UNINITIALIZED_ERROR;
  set.remove(party_index);
  for (int i = 0; i < n_parties; i++) {
    if (!set.has(i)) continue;
    if (rv = send_impl(i, in[i])) return rv;
  }
  return SUCCESS;
```

**File:** include-internal/cbmpc/internal/protocol/mpc_job.h (L307-310)
```text
  template <typename... MSGS>
  error_t plain_broadcast(MSGS&... msgs) {
    return group_message(party_set_t::all(), party_set_t::all(), msgs...);
  }
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L282-291)
```cpp
  int representative_quorum_pid_index = -1;
  std::set<crypto::pname_t> quorum_pname_set;
  for (int j = 0; j < n; j++) {
    all_pids[j] = job.get_pid(j);
    if (quorum_party_set.has(j)) {
      quorum_pids[j] = job.get_pid(j);
      quorum_pname_set.insert(job.get_name(j));
      quorum_count++;
      representative_quorum_pid_index = j;
    }
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L360-367)
```cpp
  auto h_all = job.uniform_msg<buf256_t>();
  std::map<party_idx_t, buf_t> all_received_c_s;
  for (int j = 0; j < n; j++) {
    if (!quorum_party_set.has(j)) continue;

    all_received_c_s[j] = c_all._j;
  }
  h_all._i = crypto::sha256_t::hash(all_received_c_s, quorum_pids, sid);
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L369-370)
```cpp
  if (rv = job.plain_broadcast(h_all, ac_pub_all, pi_r_all, rho_all, xij))
    return coinbase::error(rv, "Failed to broadcast h_all, ac_pub_all, pi_r_all, rho_all, xij");
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L372-398)
```cpp
  std::map<party_idx_t, buf_t> cs;
  for (int j = 0; j < n; j++) {
    if (j == i) continue;
    if (!quorum_party_set.has(j)) continue;

    if (h_all._j != h_all.received(representative_quorum_pid_index)) return coinbase::error(E_CRYPTO, "h_all mismatch");

    crypto::commitment_t com_R_tag(quorum_pids[j]);
    // deviation from the spec: since we are sending `c` to all parties, we open them for all parties.
    // furthermore, later on we compute the hash and check if the hash with the cs is correct.
    std::vector<ecc_point_t> Rs;
    for (const auto& [node_name, internal_pub_shares] : ac_pub_all._j) {
      Rs.push_back(internal_pub_shares);
    }
    com_R_tag.set(rho_all._j, c_all._j);
    if (rv = com_R_tag.open(Rs, pi_r_all._j)) return coinbase::error(rv, "Failed to open com_R_tag");

    cs[j] = c_all._j;
    // Verifying that R values are on the curve and subgroup is done in the zk verify function
    if (rv = pi_r_all._j.verify(Rs, sid, j)) return coinbase::error(rv, "Failed to verify pi_r_all");
    if (is_refresh) {
      ac_pub_all._j[ac.root->name] = curve.infinity();
    }
    ecc_point_t Qj = ac_pub_all._j.at(ac.root->name);
    if (rv = ac.verify_share_against_ancestors_pub_data(Qj, xij._j, ac_pub_all._j, job.get_name(i)))
      return coinbase::error(rv, "Failed to verify share against ancestors pub data");
  }
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L400-403)
```cpp
  if (!quorum_party_set.has(i)) {
    if (h_all.received(representative_quorum_pid_index) != crypto::sha256_t::hash(cs, quorum_pids, sid))
      return coinbase::error(E_CRYPTO, "h_all mismatch");
  }
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L428-435)
```cpp
  {
    crypto::vartime_scope_t vartime_scope;
    ecc_point_t reconstructed_Q;
    if (rv = ac.reconstruct_exponent(Qis, reconstructed_Q))
      return coinbase::error(rv, "Failed to reconstruct exponent");
    if (reconstructed_Q != Q) return coinbase::error(E_CRYPTO, "Q mismatch");
  }
  if (x_i * G != Qis[job.get_name(i)]) return coinbase::error(E_CRYPTO, "x_i * G != Qis[job.get_name(i)]");
```
