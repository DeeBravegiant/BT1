### Title
Unbounded O(|drepDelegs| × log|accountsMap|) Work in `ConwayUnRegDRep` Certificate Processing via `clearDRepDelegations` — (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

A DRep owner can submit a single `ConwayUnRegDRep` certificate that triggers `clearDRepDelegations`, which iterates over every staking credential in `drepDelegs` and calls `Map.adjust` on the full `accountsMap`. Because `|drepDelegs|` is stored in ledger state and is not bounded by any protocol parameter, and because native Haskell ledger-rule evaluation is not metered by `maxTxExUnits`, a single small transaction can force O(N × log M) work where N is the number of delegators and M is the total number of registered accounts.

---

### Finding Description

In `conwayGovCertTransition`, the `ConwayUnRegDRep` branch defines:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
```

and applies it as:

```haskell
certState'
  & certDStateL . accountsL . accountsMapL
    %~ clearDRepDelegations (drepDelegs dRepState)
``` [1](#0-0) 

`drepDelegs` is a `Set (Credential Staking)` field of `DRepState`: [2](#0-1) 

The set grows by one entry each time any stake credential delegates to this DRep (via `unDelegReDelegDRep`): [3](#0-2) 

There is no protocol parameter, ledger rule, or STS guard that caps `|drepDelegs|`. The `maxTxSize` check bounds only the serialized transaction bytes; the `maxTxExUnits` / `maxBlockExUnits` limits apply exclusively to Plutus script execution. Native Haskell ledger-rule evaluation — including `clearDRepDelegations` — runs entirely outside the ExUnits budget.

The `ConwayUnRegDRep` certificate itself is tiny (one credential + one `Coin`). The expensive work is driven entirely by ledger state accumulated before the deregistration transaction is submitted.

---

### Impact Explanation

A single `ConwayUnRegDRep` certificate causes O(|drepDelegs| × log|accountsMap|) native Haskell work during block validation. With N delegators and M total accounts, each `Map.adjust` call is O(log M), and there are N such calls. Neither N nor M is bounded per-certificate. This directly exceeds the intended per-certificate validation cost model, which assumes O(log N) work per certificate. Block validation time for any block containing this certificate becomes proportional to the number of delegators, which can be made arbitrarily large by the attacker in advance.

This matches the allowed Medium impact: *attacker-controlled certificates exceed intended validation limits*.

---

### Likelihood Explanation

**Attacker prerequisites:**
1. Register a DRep — permissionless, costs the `dRepDeposit` protocol parameter (currently 500 ADA on mainnet, refunded on deregistration).
2. Accumulate delegators — each stake credential delegation costs a 2 ADA key deposit plus transaction fees. The attacker controls the DRep credential and can attract delegators organically, or fund the delegations themselves.
3. Submit one `ConwayUnRegDRep` certificate — the DRep owner's signature is the only authorization required.

The economic barrier is real (2 ADA × N delegators), but:
- The DRep deposit is fully refunded on deregistration, so the net cost is only the stake-key deposits and fees.
- A popular legitimate DRep with many organic delegators can trigger the same condition without any upfront cost beyond the DRep deposit.
- The attack can be staged over many epochs and executed in a single block.

---

### Recommendation

1. **Remove `drepDelegs` from `DRepState` entirely.** The reverse-delegation index is a derived structure. On deregistration, instead of iterating over `drepDelegs` to clear account pointers, clear the pointer lazily (e.g., treat a missing DRep entry as "no delegation") or use a single `Map.filterWithKey` pass over `accountsMap` bounded by block-level work limits.
2. **Alternatively**, cap `|drepDelegs|` via a new protocol parameter `maxDRepDelegators` and enforce it at delegation time.
3. **At minimum**, replace the `foldr Map.adjust` with a single `Map.mapWithKey` or `Map.differenceWith` pass that is O(M) rather than O(N × log M), and document the cost explicitly.

---

### Proof of Concept

```
1. Register DRep credential D (pay dRepDeposit, e.g. 500 ADA).
2. Register K stake credentials S_1 … S_K (pay 2 ADA each).
3. For each S_i, submit a DelegVote certificate delegating to D.
   → drepDelegs D = {S_1, …, S_K}  (stored in ledger state)
4. Submit a transaction containing ConwayUnRegDRep D refund.
   → clearDRepDelegations {S_1,…,S_K} accountsMap
   → K calls to Map.adjust, each O(log |accountsMap|)
   → total native Haskell work: O(K × log M), unbounded by any protocol parameter.
```

Benchmark: run `conwayGovCertTransition` with `ConwayUnRegDRep` against a synthetic `CertState` where `drepDelegs` has 10 000, 100 000, and 1 000 000 entries and assert that wall-clock time stays within the block slot budget (~1 s). The assertion will fail at large K, demonstrating the unbounded cost.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L246-254)
```haskell
        clearDRepDelegations delegs accountsMap =
          foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
      pure $
        case mDRepState of
          Nothing -> certState'
          Just dRepState ->
            certState'
              & certDStateL . accountsL . accountsMapL
                %~ clearDRepDelegations (drepDelegs dRepState)
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs (L166-171)
```haskell
data DRepState = DRepState
  { drepExpiry :: !EpochNo
  , drepAnchor :: !(StrictMaybe Anchor)
  , drepDeposit :: !(CompactForm Coin)
  , drepDelegs :: !(Set (Credential Staking))
  }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L129-143)
```haskell
unDelegReDelegDRep stakeCred accountState mNewDRep =
  fromMaybe (vsDRepsL %~ addNewDelegation) $ do
    dRep@(DRepCredential dRepCred) <- accountState ^. dRepDelegationAccountStateL
    pure $
      -- There is no need to update set of delegations if delegation is unchanged
      if Just dRep == mNewDRep
        then id
        else
          vsDRepsL %~ addNewDelegation . Map.adjust (drepDelegsL %~ Set.delete stakeCred) dRepCred
  where
    addNewDelegation =
      case mNewDRep of
        Just (DRepCredential dRepCred) ->
          Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
        _ -> id
```
