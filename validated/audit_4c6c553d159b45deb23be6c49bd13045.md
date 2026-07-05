### Title
`RequireGuard (KeyHashObj kh)` Native Script Satisfiable Without Key Signature — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`)

---

### Summary

The Dijkstra era introduces a new native script constructor `DijkstraRequireGuard (Credential Guard)` and a corresponding `guards` field in the transaction body. When the credential is a key hash (`KeyHashObj kh`), the script is evaluated by checking whether `KeyHashObj kh` is present in the transaction's `guards` set. However, the ledger never validates that key-hash credentials in `guards` are backed by a corresponding VKey witness. Any unprivileged transaction author can include an arbitrary `KeyHashObj kh` in the `guards` field and thereby satisfy any `RequireGuard (KeyHashObj kh)` script — without possessing or providing the private key for `kh`. This is a direct analog to M-16's open-ended operator set: the "authorized minter/spender" role is not hardcoded or cryptographically enforced for key-hash guards.

---

### Finding Description

**Root cause — `evalDijkstraNativeScript`:**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs
evalDijkstraNativeScript keyHashes (ValidityInterval txStart txExp) guards = go
  where
    go = \case
      RequireSignature hash -> hash `Set.member` keyHashes   -- validated against VKey witnesses
      RequireGuard     cred -> cred `OSet.member` guards     -- validated only against tx body field
      ...
```

`keyHashes` is derived from actual VKey witnesses (cryptographically verified signatures). `guards` is taken directly from `guardsTxBodyL` — a field the transaction author populates freely with no independent validation for key-hash entries. [1](#0-0) 

**Root cause — `getDijkstraScriptsNeeded` omits key-hash guards from witness requirements:**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
guardingScriptsNeeded = AlonzoScriptsNeeded $
  catMaybes $
    zipAsIxItem (txb ^. guardsTxBodyL) $
      \(AsIxItem idx cred) ->
        (\sh -> (GuardingPurpose (AsIxItem idx sh), sh)) <$> credScriptHash cred
```

`credScriptHash` returns `Nothing` for `KeyHashObj` credentials, so key-hash guards are silently dropped from `scriptsNeeded`. They are never added to the set of required witnesses. [2](#0-1) 

**Root cause — `dijkstraUtxowTransition` does not require key-hash guards to be witnessed:**

The UTXOW rule calls `Shelley.validateNeededWitnesses`, which uses `getConwayWitsVKeyNeeded`. That function does not include guard key hashes in the required witness set. No other check in `dijkstraUtxowTransition` validates key-hash credentials in `guards` against VKey witnesses. [3](#0-2) 

**Contrast with `RequireSignature`:** `RequireSignature hash` checks `hash ∈ keyHashes` where `keyHashes` is the set of hashes of keys that actually signed the transaction body. `RequireGuard (KeyHashObj kh)` checks `KeyHashObj kh ∈ guards` where `guards` is a field the attacker writes themselves. The security guarantee is entirely absent for the key-hash case.

**Confirmed by the existing test suite:**

```haskell
-- eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/UtxowSpec.hs
it "Spending inputs locked by script requiring a keyhash guard" $ do
  guardKeyHash <- KeyHashObj <$> freshKeyHash
  scriptHash   <- impAddNativeScript (RequireGuard guardKeyHash)
  txIn         <- produceScript scriptHash
  let tx = mkBasicTx (mkBasicTxBody & inputsTxBodyL .~ [txIn])
  submitFailingTx tx [...]                                    -- fails without guard in body
  submitTx_ $ tx & bodyTxL . guardsTxBodyL .~ [guardKeyHash] -- succeeds with guard in body, NO signature added
```

The passing transaction adds `guardKeyHash` to the `guards` field but adds **no VKey witness** for that key hash. The test confirms the bypass is accepted by the ledger. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.**

1. **Minting policy bypass:** A native asset minting policy expressed as `RequireGuard (KeyHashObj kh)` (or any compound script containing it) can be satisfied by any transaction author who includes `KeyHashObj kh` in the `guards` field. The attacker can mint or burn arbitrary quantities of the protected asset without holding the key `kh`. This is direct unauthorized creation of native assets.

2. **UTxO spending bypass:** Any UTxO locked by a script containing `RequireGuard (KeyHashObj kh)` can be spent by any transaction that includes `KeyHashObj kh` in `guards`. This is direct unauthorized loss of funds.

3. **Certificate/withdrawal bypass:** Any certificate or reward withdrawal protected by such a script is similarly unprotected.

---

### Likelihood Explanation

**High.** The attacker-controlled entry path requires only the ability to submit a transaction — no privileged role, no governance majority, no leaked key. The attacker:

1. Identifies a minting policy or UTxO-locking script that uses `RequireGuard (KeyHashObj kh)` for any key hash `kh`.
2. Constructs a transaction with `KeyHashObj kh` in `guardsTxBodyL`.
3. Submits the transaction. The ledger accepts it.

The `guards` field is part of the serialized transaction body, fully attacker-controlled. The existing test suite already demonstrates the exact exploit path succeeds.

---

### Recommendation

Key-hash credentials in the `guards` field must be validated against VKey witnesses, mirroring the treatment of `RequireSignature`. Specifically:

1. In `getDijkstraScriptsNeeded` (or a parallel `getWitsVKeyNeeded` override for Dijkstra), collect all `KeyHashObj kh` credentials from `guardsTxBodyL` and add them to the required key-witness set.
2. Alternatively, restrict `DijkstraRequireGuard` to accept only `ScriptHashObj` credentials (script-hash guards are already properly validated via `scriptsNeeded`), and require key-hash guard authorization to be expressed via `RequireSignature` instead.

The fix is analogous to the M-16 recommendation: hardcode (or cryptographically enforce) the authorized set of guard credentials rather than allowing the transaction author to assert them freely.

---

### Proof of Concept

```
-- Attacker controls no keys. Target: UTxO locked by RequireGuard (KeyHashObj victimKey)
-- or minting policy = RequireGuard (KeyHashObj victimKey)

-- Step 1: Observe that policyID = hash(RequireGuard (KeyHashObj victimKey))
-- Step 2: Construct transaction:
--   inputs  = [targetUTxO]          -- or mint field = [(policyID, +N tokens)]
--   guards  = [KeyHashObj victimKey] -- attacker writes this freely
--   witsTxL = {}                     -- NO signature from victimKey
-- Step 3: Submit. Ledger accepts because:
--   evalDijkstraNativeScript _ _ {KeyHashObj victimKey} (RequireGuard (KeyHashObj victimKey))
--   = KeyHashObj victimKey ∈ {KeyHashObj victimKey}
--   = True
-- Result: UTxO spent / tokens minted without victimKey's authorization.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L555-576)
```haskell
evalDijkstraNativeScript ::
  (DijkstraEraScript era, NativeScript era ~ DijkstraNativeScript era) =>
  Set.Set (KeyHash Witness) ->
  ValidityInterval ->
  OSet (Credential Guard) ->
  NativeScript era ->
  Bool
evalDijkstraNativeScript keyHashes (ValidityInterval txStart txExp) guards = go
  where
    -- the evaluation will stop as soon as it reaches the required number of valid scripts
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
    go = \case
      RequireTimeStart lockStart -> lockStart `lteNegInfty` txStart
      RequireTimeExpire lockExp -> txExp `ltePosInfty` lockExp
      RequireSignature hash -> hash `Set.member` keyHashes
      RequireAllOf xs -> all go xs
      RequireAnyOf xs -> any go xs
      RequireMOf m xs -> isValidMOf m xs
      RequireGuard cred -> cred `OSet.member` guards
      _ -> error "Impossible: All NativeScripts should have been accounted for"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L166-177)
```haskell
getDijkstraScriptsNeeded ::
  (DijkstraEraTxBody era, DijkstraEraScript era) =>
  UTxO era -> TxBody l era -> AlonzoScriptsNeeded era
getDijkstraScriptsNeeded utxo txb =
  getConwayScriptsNeeded utxo txb
    <> guardingScriptsNeeded
  where
    guardingScriptsNeeded = AlonzoScriptsNeeded $
      catMaybes $
        zipAsIxItem (txb ^. guardsTxBodyL) $
          \(AsIxItem idx cred) -> (\sh -> (GuardingPurpose (AsIxItem idx sh), sh)) <$> credScriptHash cred

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L219-301)
```haskell
dijkstraUtxowTransition = do
  TRC (DijkstraUtxoEnv slot pp certState originalUtxo, u, stAnnTx) <- judgmentContext
  let tx = stAnnTx ^. txStAnnTxG
      scriptsProvided = scriptsProvidedStAnnTx stAnnTx

  let txBody = tx ^. bodyTxL
      subTxs = OMap.elems $ txBody ^. subTransactionsTxBodyL
      witsKeyHashes = keyHashWitnessesTxWits (tx ^. witsTxL)

  -- All lookups use originalUtxo.
  -- A subtx may consume a txout that the top-level tx references, so the UTXO threaded in the state may not contain it.

  -- scriptsNeeded for the top-level tx
  let topScriptsNeeded = scriptsNeededStAnnTx stAnnTx
      topScriptHashesNeeded = getScriptsHashesNeeded topScriptsNeeded
      subStAnnTxs = subTransactionsStAnnTx stAnnTx

  -- scriptsNeeded aggregated across all levels
  let allScriptHashesNeeded =
        Set.unions $
          topScriptHashesNeeded
            : (getScriptsHashesNeeded . scriptsNeededStAnnTx <$> subStAnnTxs)

  {- ∀s ∈ (txscripts txw utxo neededHashes ) ∩ Scriptph1 , validateScript s tx -}
  -- Per-level: phase-1 script validation is per-tx (script execution)
  runTest $ Babbage.validateFailedBabbageScripts tx scriptsProvided topScriptHashesNeeded

  {- neededHashes − dom(refScripts tx utxo) = dom(txwitscripts txw) -}
  -- Aggregated: missing/extraneous scripts across all levels.
  let witnessScripts =
        Map.keysSet (tx ^. witsTxL . scriptTxWitsL)
          <> foldMap (Map.keysSet . (^. witsTxL . scriptTxWitsL)) subTxs
      allRefScriptInputs =
        txBody ^. referenceInputsTxBodyL
          <> txBody ^. inputsTxBodyL
          <> foldMap
            ( \subTx ->
                subTx ^. bodyTxL . referenceInputsTxBodyL
                  <> subTx ^. bodyTxL . inputsTxBodyL
            )
            subTxs
      refScripts = Map.keysSet $ getReferenceScripts originalUtxo allRefScriptInputs
  runTest $ Babbage.babbageMissingScripts pp allScriptHashesNeeded refScripts witnessScripts

  {-  inputHashes ⊆  dom(txdats txw) ⊆  allowed -}
  -- Per-level: datum check for top-level tx's own spend inputs
  runTest $ Alonzo.missingRequiredDatums scriptsProvided originalUtxo tx

  {- dom (txrdmrs tx) = { rdptr txb sp | (sp, h) ∈ scriptsNeeded utxo tx,
                          h ↦ s ∈ txscripts txw, s ∈ Scriptph2} -}
  -- Per-level: redeemer indexing is per-tx
  runTest $ Alonzo.hasExactSetOfRedeemers tx scriptsProvided topScriptsNeeded

  -- check VKey witnesses
  {- ∀ (vk ↦ σ) ∈ (txwitsVKey txw), V_vk⟦ txbodyHash ⟧_σ -}
  runTestOnSignal $ Shelley.validateVerifiedWits tx

  {- witsVKeyNeeded utxo tx genDelegs ⊆ witsKeyHashes -}
  runTest $ Shelley.validateNeededWitnesses witsKeyHashes certState originalUtxo txBody

  -- check metadata hash
  {- ((adh = ◇) ∧ (ad= ◇)) ∨ (adh = hashAD ad) -}
  runTestOnSignal $ Shelley.validateMetadata pp tx

  {- ∀x ∈ range(txdats txw) ∪ range(txwitscripts txw) ∪ (⋃ ( , ,d,s) ∈ txouts tx {s, d}),
                       x ∈ Script ∪ Datum ⇒ isWellFormed x -}
  runTest $ Babbage.validateScriptsWellFormed pp tx

  {- scriptIntegrityHash txb = hashScriptIntegrity pp (languages txw) (txrdmrs txw) -}
  -- Per-level: script integrity is per-tx (depends on that tx's redeemers and language views)
  let scriptIntegrity = mkScriptIntegrity pp tx (plutusLanguagesUsedStAnnTx stAnnTx)
  runTest $ Alonzo.checkScriptIntegrityHash tx pp scriptIntegrity

  {- concatMapˡ (λ txSub → mapˢ proj₁ (TopLevelGuardsOf txSub)) (SubTransactionsOf txTop) ⊆ GuardsOf txTop -}
  let requiredGuardsBySubTxs =
        foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
      topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
      missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
  runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards

  -- Pass through to UTXO sub-rule, carrying the original UTxO
  trans @(EraRule "UTXO" era) $
    TRC (DijkstraUtxoEnv slot pp certState originalUtxo, u, stAnnTx)
```

**File:** eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/UtxowSpec.hs (L30-38)
```haskell
    it "Spending inputs locked by script requiring a keyhash guard" $ do
      guardKeyHash <- KeyHashObj <$> freshKeyHash
      scriptHash <- impAddNativeScript (RequireGuard guardKeyHash)
      txIn <- produceScript scriptHash
      let tx = mkBasicTx (mkBasicTxBody & inputsTxBodyL .~ [txIn])
      submitFailingTx
        tx
        [injectFailure $ Conway.ScriptWitnessNotValidatingUTXOW $ NES.singleton scriptHash]
      submitTx_ $ tx & bodyTxL . guardsTxBodyL .~ [guardKeyHash]
```
