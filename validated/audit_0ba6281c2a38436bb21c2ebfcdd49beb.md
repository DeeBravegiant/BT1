### Title
`RequireGuard` Key Hash Credentials Lack Signature Validation, Enabling Permissionless Guard Satisfaction — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`)

---

### Summary

In the Dijkstra era, the `RequireGuard` native script constructor evaluates to `True` whenever a credential appears in the transaction's `guards` set. For **key hash credentials**, no corresponding VKey witness is required. Any unprivileged transaction submitter can satisfy `RequireGuard (KeyHashObj kh)` by simply inserting `kh` into their transaction's `guards` field — without possessing the private key. This is the direct Cardano analog of the external report's permissionless two-step execution: the "execution step" (satisfying the guard) is open to anyone.

---

### Finding Description

**Root cause — `evalDijkstraNativeScript`:** [1](#0-0) 

```haskell
RequireGuard cred -> cred `OSet.member` guards
```

The evaluation only checks set membership. It does not verify that a `KeyHashObj kh` credential actually signed the transaction body.

**The `guards` field is attacker-controlled:**

The `guards` field is part of the `DijkstraTxBodyRaw` and is freely writable by any transaction author. [2](#0-1) 

**The UTXOW rule never validates key hash guards:**

`dijkstraUtxowTransition` calls `validateNeededWitnesses`, which delegates to `getWitsVKeyNeeded`. That function collects required VKey witnesses from inputs, withdrawals, certificates, pool owners, and `reqSignerHashes` — but **not** from the `guards` set. [3](#0-2) 

The only guard-related check in the UTXOW rule is `MissingRequiredGuards`, which only verifies that sub-transaction-required guards are *present* in the top-level `guards` set — it does not validate those credentials: [4](#0-3) 

**Contrast with native script guards (script hash credentials):** When a guard credential is a `ScriptHashObj`, the script is included in `scriptsNeeded` and validated by `validateFailedBabbageScripts`, producing `ScriptWitnessNotValidatingUTXOW` on failure. Key hash guards receive no equivalent treatment.

**The existing test proves the attack path:** [5](#0-4) 

```haskell
submitTx_ $ tx & bodyTxL . guardsTxBodyL .~ [guardKeyHash]
```

This succeeds with zero witnesses for `guardKeyHash`. The key never signs anything.

---

### Impact Explanation

Any UTxO, minting policy, withdrawal, or certificate locked by a `RequireGuard (KeyHashObj kh)` native script can be spent or executed by any unprivileged attacker who inserts `kh` into their transaction's `guards` set. This is a **direct, unconditional loss of ADA or native assets** through an invalid ledger state transition — matching the Critical impact tier.

---

### Likelihood Explanation

`RequireGuard` is presented as a first-class native script constructor alongside `RequireSignature`. A developer who uses `RequireGuard (KeyHashObj kh)` expecting it to behave like `RequireSignature kh` (i.e., requiring the key to sign) will have all protected funds stolen. The attack requires no special role, no governance majority, and no key material — only the ability to submit a transaction.

---

### Recommendation

Key hash credentials appearing in the `guards` set must be added to the set of required VKey witnesses, analogously to `RequireSignature`. Concretely, `getWitsVKeyNeeded` (or the Dijkstra-era override) should union the key hashes from `txBody ^. guardsTxBodyL` into the required witness set, so that `validateNeededWitnesses` enforces their presence. [6](#0-5) 

---

### Proof of Concept

1. Alice locks a UTxO with script `RequireGuard (KeyHashObj aliceKH)`.
2. Bob constructs a transaction spending Alice's UTxO and sets `guards = [aliceKH]` — no witness for `aliceKH`.
3. `evalDijkstraNativeScript` evaluates `RequireGuard aliceKH` → `aliceKH ∈ guards` → `True`. Script passes.
4. `validateNeededWitnesses` does not require a witness for `aliceKH` (guards are not in `getWitsVKeyNeeded`).
5. Transaction is accepted. Bob steals Alice's funds without her key.

This is mechanically identical to the external report's attack: the "execution step" (guard satisfaction) is permissionless because the ledger never checks that the guard key authorized the transaction.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L568-576)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L175-175)
```haskell
    , dtbrGuards :: !(OSet (Credential Guard))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L276-277)
```haskell
  {- witsVKeyNeeded utxo tx genDelegs ⊆ witsKeyHashes -}
  runTest $ Shelley.validateNeededWitnesses witsKeyHashes certState originalUtxo txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L292-297)
```haskell
  {- concatMapˡ (λ txSub → mapˢ proj₁ (TopLevelGuardsOf txSub)) (SubTransactionsOf txTop) ⊆ GuardsOf txTop -}
  let requiredGuardsBySubTxs =
        foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
      topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
      missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
  runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards
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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxow.hs (L412-423)
```haskell
validateNeededWitnesses ::
  EraUTxO era =>
  -- | Provided witness
  Set (KeyHash Witness) ->
  CertState era ->
  UTxO era ->
  TxBody t era ->
  Test (ShelleyUtxowPredFailure era)
validateNeededWitnesses witsKeyHashes certState utxo txBody =
  let needed = getWitsVKeyNeeded certState utxo txBody
      missingWitnesses = Set.difference needed witsKeyHashes
   in failureOnNonEmptySet missingWitnesses MissingVKeyWitnessesUTXOW
```
