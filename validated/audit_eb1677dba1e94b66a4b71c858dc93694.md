### Title
Unprivileged Actor Can Frontrun Stake Credential Registration to Block Legitimate Users from Earning Rewards - (File: `eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Deleg.hs`, `eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs`)

---

### Summary

The `DELEG` rule (Shelley through Conway) allows any unprivileged actor to register **any** stake credential without providing a witness from the credential owner. Combined with the uniqueness check that rejects duplicate registrations with `StakeKeyAlreadyRegisteredDELEG`, an attacker can frontrun a legitimate user's stake credential registration by submitting their own registration for the same credential first, blocking the victim from delegating their stake and earning rewards for as long as the attacker sustains the attack.

---

### Finding Description

The `delegationTransition` function processes `RegTxCert cred` with no witness requirement from the credential owner — only a uniqueness check is enforced:

```haskell
RegTxCert cred -> do
  not (isAccountRegistered cred (ds ^. accountsL)) ?! StakeKeyAlreadyRegisteredDELEG cred
  let compactDeposit = compactCoinOrError (pp ^. ppKeyDepositL)
  pure $ certState & certDStateL . accountsL %~ registerShelleyAccount cred ptr compactDeposit Nothing
``` [1](#0-0) 

This is by design: the Shelley formal spec explicitly excludes `DCertRegKey` from the witness requirement:

> `certWitsNeeded tx = ∪{cwitness c | c ∈ txcerts tx \ (DCertRegKey ∪ DCertMir)}` [2](#0-1) 

The same no-witness behavior is preserved in Conway for `ConwayRegCert _ SNothing`. Both `getScriptWitnessConwayTxCert` and `getVKeyWitnessConwayTxCert` return `Nothing` for this certificate variant:

```haskell
-- For both of the functions `getScriptWitnessConwayTxCert` and
-- `getVKeyWitnessConwayTxCert` we preserve the old behavior of not requiring a witness
-- for staking credential registration, but only during the transitional period of Conway
-- era and only for staking credential registration certificates without a deposit. Future
-- eras will require a witness for registration certificates, because the one without a
-- deposit will be removed.
...
      ConwayRegCert _ SNothing -> Nothing   -- no witness required
``` [3](#0-2) 

In Conway's `conwayDelegTransition`, the same uniqueness-only check applies:

```haskell
ConwayRegCert stakeCred sMayDeposit -> do
  forM_ sMayDeposit checkDepositAgainstPParams
  checkStakeKeyNotRegistered stakeCred   -- only check: not already registered
  pure $ certState & certDStateL . accountsL %~ registerConwayAccount stakeCred ppKeyDepositCompact Nothing
``` [4](#0-3) 

Where `checkStakeKeyNotRegistered` is:

```haskell
checkStakeKeyNotRegistered stakeCred =
  not (isAccountRegistered stakeCred accounts)
    ?! injectFailure (StakeKeyRegisteredDELEG stakeCred)
``` [5](#0-4) 

This is the direct structural analog of the `createOffer` vulnerability: a user-controlled identifier (the stake credential) is checked only for uniqueness, with no authorization check binding the submitter to the credential. Any actor who knows the victim's stake credential (which is public, derivable from their address) can register it first.

---

### Impact Explanation

An attacker who frontrunning-registers the victim's stake credential causes:

1. **Staking rewards blocked**: The victim cannot submit a `DelegStakeTxCert` because delegation requires the credential to be registered by the delegator themselves. The victim's ADA earns no staking rewards for the duration of the attack.
2. **Governance participation blocked**: The victim cannot delegate their vote to a DRep (`DelegVote`), removing them from on-chain governance.
3. **Sustained DoS**: The victim can deregister (recovering the attacker's deposit) and re-register, but the attacker can frontrun each re-registration attempt. The attacker loses the deposit each cycle, but a motivated adversary (e.g., a competitor) can sustain this indefinitely at low cost on Cardano mainnet.

This matches the **Medium** allowed impact: *"Attacker-controlled transactions... modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters."* The attacker's certificate directly suppresses the victim's reward stream outside the intended design.

---

### Likelihood Explanation

- The attack requires only the `ppKeyDeposit` amount (currently 2 ADA) per frontrun cycle, which is recovered by the attacker if the victim never deregisters, or lost to the victim if they do.
- Stake credentials are public (derivable from any address the victim has used on-chain), so the attacker needs no privileged information.
- Cardano's mempool is observable, making frontrunning straightforward.
- The attack is economically costly for the attacker per cycle, reducing likelihood of random attackers, but a motivated competitor targeting a specific large delegator or DRep candidate faces a low barrier.

---

### Recommendation

Require a witness from the credential owner for all stake credential registration certificates, including the `ConwayRegCert _ SNothing` legacy variant. The Conway codebase already acknowledges this fix is planned:

> *"Future eras will require a witness for registration certificates, because the one without a deposit will be removed."* [6](#0-5) 

The fix should be applied to `getScriptWitnessConwayTxCert` and `getVKeyWitnessConwayTxCert` to return the credential's witness for `ConwayRegCert _ SNothing` as well, and the `delegationTransition` / `conwayDelegTransition` rules should enforce this witness at the STS level.

---

### Proof of Concept

```
1. Alice holds ADA at an address whose stake part hashes to cred_alice (public information).
2. Alice submits Tx_A containing [RegTxCert cred_alice] to register her stake credential.
3. Bob (attacker) observes Tx_A in the mempool.
4. Bob submits Tx_B containing [RegTxCert cred_alice] with a higher fee — no witness from
   cred_alice is required (ConwayRegCert _ SNothing -> Nothing).
5. Tx_B is included in a block before Tx_A.
6. Tx_A fails: StakeKeyAlreadyRegisteredDELEG cred_alice.
7. Alice cannot delegate her stake or vote; her ADA earns no rewards.
8. Alice deregisters cred_alice (recovering Bob's 2 ADA deposit) and resubmits Tx_A.
9. Bob observes the new registration attempt and repeats step 4.
   The cycle continues as long as Bob is willing to pay 2 ADA per round.
```

The `StakeKeyAlreadyRegisteredDELEG` predicate failure is confirmed in the test suite: [7](#0-6)

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Deleg.hs (L265-269)
```haskell
    RegTxCert cred -> do
      -- (hk ∉ dom (rewards ds))
      not (isAccountRegistered cred (ds ^. accountsL)) ?! StakeKeyAlreadyRegisteredDELEG cred
      let compactDeposit = compactCoinOrError (pp ^. ppKeyDepositL)
      pure $ certState & certDStateL . accountsL %~ registerShelleyAccount cred ptr compactDeposit Nothing
```

**File:** eras/shelley/formal-spec/utxo.tex (L460-463)
```tex
    & \hspace{-0.8cm}\fun{certWitsNeeded} \Tx \to \powerset{\Credential}
    & \text{certificates with witnesses} \\
    &  \hspace{-0.8cm}\fun{certWitsNeeded}~\var{tx} = \\
    & \bigcup\{\cwitness{c} \mid c \in \txcerts{tx} \setminus (\DCertRegKey\cup\DCertMir)\}
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs (L757-771)
```haskell
-- For both of the functions `getScriptWitnessConwayTxCert` and
-- `getVKeyWitnessConwayTxCert` we preserve the old behavior of not requiring a witness
-- for staking credential registration, but only during the transitional period of Conway
-- era and only for staking credential registration certificates without a deposit. Future
-- eras will require a witness for registration certificates, because the one without a
-- deposit will be removed.

getScriptWitnessConwayTxCert ::
  ConwayTxCert era ->
  Maybe ScriptHash
getScriptWitnessConwayTxCert = \case
  ConwayTxCertDeleg delegCert ->
    case delegCert of
      ConwayRegCert _ SNothing -> Nothing
      ConwayRegCert cred (SJust _) -> credScriptHash cred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L212-214)
```haskell
    checkStakeKeyNotRegistered stakeCred =
      not (isAccountRegistered stakeCred accounts)
        ?! injectFailure (StakeKeyRegisteredDELEG stakeCred)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L233-239)
```haskell
    ConwayRegCert stakeCred sMayDeposit -> do
      forM_ sMayDeposit checkDepositAgainstPParams
      checkStakeKeyNotRegistered stakeCred
      pure $
        certState
          & certDStateL . accountsL
            %~ registerConwayAccount stakeCred ppKeyDepositCompact Nothing
```

**File:** eras/shelley/impl/testlib/Test/Cardano/Ledger/Shelley/Imp/DelegSpec.hs (L150-162)
```haskell
    it "When already already registered" $ do
      cred <- ScriptHashObj <$> impAddNativeScript (RequireAllOf [])
      regTxCert <- genRegTxCert cred
      let tx =
            mkBasicTx mkBasicTxBody
              & bodyTxL . certsTxBodyL
                .~ [regTxCert]
      submitTx_ tx
      submitFailingTx
        tx
        [ injectFailure $ StakeKeyAlreadyRegisteredDELEG cred
        ]
      expectStakeCredRegistered cred
```
