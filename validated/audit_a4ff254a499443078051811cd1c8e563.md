### Title
Unauthorized Direct Deposits to Any Registered Staking Account Without Owner Authorization — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs`, `libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs`)

---

### Summary

In the Dijkstra era, the new `directDepositsTxBodyL` transaction body field allows any unprivileged transaction sender to add ADA to any registered staking account without requiring a witness (key signature or script execution) from the target account owner. The ENTITIES/SUBENTITIES rules validate only that the target accounts are registered, but impose no authorization check. This is the direct analog of the `RollerPeriphery.approve()` missing-access-control pattern: a sensitive state-mutation operation (modifying account balances) is callable by anyone without the owner's consent.

---

### Finding Description

The Dijkstra era introduces `DirectDeposits` as a new transaction body field (CBOR map key 25), defined as a map from `AccountAddress` to `Coin`. [1](#0-0) 

The `DijkstraEraTxBody` class exposes this field for both top-level and sub-transactions: [2](#0-1) 

In `dijkstraEntitiesTransition`, the only validation applied to direct deposits is `directDepositsMissingAccounts`, which checks that target accounts are registered — nothing more: [3](#0-2) 

The same pattern appears in `dijkstraSubEntitiesTransition` for sub-transactions: [4](#0-3) 

The `applyDirectDeposits` function itself explicitly documents that no authorization checks are performed: [5](#0-4) 

Critically, the UTXOW witness-collection function `getConwayWitsVKeyNeeded` (inherited by Dijkstra) collects witnesses for UTxO inputs, withdrawals, certificates, and voter procedures — but **not** for direct deposit targets: [6](#0-5) 

The Dijkstra UTXOW rule calls `validateNeededWitnesses` using this function, so no witness from the target account owner is ever required for a direct deposit: [7](#0-6) 

By contrast, every other operation that modifies an account's state — withdrawal, delegation, deregistration — requires a witness from the account owner. Direct deposits are the sole exception.

---

### Impact Explanation

**Medium — Attacker-controlled transactions modify rewards/withdrawals outside design parameters.**

1. **Disruption of withdrawals in legacy mode**: In Conway-compatible (legacy) mode, `withdrawalsThatDoNotDrainAccounts` requires that a withdrawal exactly drains the account balance. An attacker observing a pending withdrawal transaction can front-run it with a direct deposit of even 1 lovelace to the same account. The victim's withdrawal then fails because the declared amount no longer equals the new balance. [8](#0-7) 

2. **Unauthorized modification of account balances**: Any transaction sender can increase any registered staking account's balance without the owner's consent. This modifies the reward/balance state of accounts outside the design parameters that require owner authorization for all account mutations.

---

### Likelihood Explanation

Any unprivileged transaction sender on the network can craft a Dijkstra-era transaction with a `directDepositsTxBodyL` entry targeting any registered staking account. No special role, key, or privilege is required beyond the ability to submit a valid transaction and pay the fee. The ADA deposited comes from the attacker's own UTxO inputs, so the attack is self-funded and requires no cooperation from the victim.

---

### Recommendation

Add the key-hash witnesses of direct deposit target accounts to the required witness set in `getConwayWitsVKeyNeeded` (or a Dijkstra-specific override), analogously to how `wdrlAuthors` is collected for withdrawals: [9](#0-8) 

For each `(AccountAddress _ (AccountId cred), _)` entry in `directDepositsTxBodyL`, require a witness for `cred` (key-hash witness or script witness as appropriate). This mirrors the existing pattern for withdrawals and ensures that only the account owner can authorize a direct deposit into their account.

---

### Proof of Concept

1. Alice has a registered staking account with balance 100 ADA. She submits a withdrawal transaction for exactly 100 ADA (legacy mode requires exact drain).
2. Bob observes Alice's transaction in the mempool.
3. Bob submits a Dijkstra-era transaction with `directDepositsTxBodyL = {Alice's account address: 1 lovelace}`, paying 1 lovelace from his own UTxO. No witness from Alice is required; the ENTITIES rule only checks `directDepositsMissingAccounts` (Alice's account is registered, so this passes).
4. Bob's transaction is included first. Alice's account balance is now 100 ADA + 1 lovelace.
5. Alice's withdrawal transaction is now invalid: `withdrawalsThatDoNotDrainAccounts` finds that 100 ADA ≠ 100 ADA + 1 lovelace, and the transaction fails with `IncompleteWithdrawals`. [10](#0-9) [8](#0-7)

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Address.hs (L990-993)
```haskell
-- | Direct deposits to account addresses.
newtype DirectDeposits = DirectDeposits {unDirectDeposits :: Map AccountAddress Coin}
  deriving (Show, Eq, Generic)
  deriving newtype (NoThunks, NFData, EncCBOR, DecCBOR)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L1319-1319)
```haskell
  directDepositsTxBodyL :: Lens' (TxBody l era) DirectDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L191-216)
```haskell
dijkstraEntitiesTransition = do
  TRC (EntitiesEnv legacyMode certsEnv, certState, certificates) <- judgmentContext
  let Conway.CertsEnv tx pp curEpoch _committee _committeeProposals = certsEnv
      withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
      accounts = certState ^. certDStateL . accountsL

  runTest $ Conway.validateWithdrawalsDelegated accounts tx

  network <- liftSTS $ asks networkId

  validateWithdrawals legacyMode network withdrawals accounts

  let certStateBeforeCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
  certStateAfterCerts <-
    trans @(EraRule "CERTS" era) $ TRC (certsEnv, certStateBeforeCerts, certificates)

  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L196-210)
```haskell
withdrawalsThatDoNotDrainAccounts ::
  EraAccounts era =>
  Withdrawals ->
  Network ->
  Accounts era ->
  -- | invalid withdrawal = that which does not have an account address or is in
  -- the wrong network.
  -- incomplete withdrawal = that which does not withdraw the exact account
  -- balance.
  Maybe (Withdrawals, Map AccountAddress (Mismatch RelEQ Coin))
withdrawalsThatDoNotDrainAccounts =
  categorizeWithdrawals
    ( \withdrawalAmount account ->
        withdrawalAmount == fromCompact (account ^. balanceAccountStateL)
    )
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L287-298)
```haskell
-- | Add each direct-deposit amount to the matching account balance.
--
-- /Note/ - There are no checks that direct deposits mention only registered accounts.
applyDirectDeposits ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Accounts era
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L189-197)
```haskell
getConwayWitsVKeyNeeded ::
  (EraTx era, ConwayEraTxBody era) =>
  UTxO era ->
  TxBody l era ->
  Set.Set (KeyHash Witness)
getConwayWitsVKeyNeeded utxo txBody =
  getShelleyWitsVKeyNeededNoGov utxo txBody
    `Set.union` Set.map asWitness (txBody ^. reqSignerHashesTxBodyG)
    `Set.union` voterWitnesses txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L276-277)
```haskell
  {- witsVKeyNeeded utxo tx genDelegs ⊆ witsKeyHashes -}
  runTest $ Shelley.validateNeededWitnesses witsKeyHashes certState originalUtxo txBody
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/UTxO.hs (L241-248)
```haskell
    wdrlAuthors :: Set (KeyHash Witness)
    wdrlAuthors = Map.foldrWithKey' accum Set.empty (unWithdrawals (txBody ^. withdrawalsTxBodyL))
      where
        accum key _ !ans =
          let cred = key ^. accountAddressCredentialL
           in case credKeyHashWitness cred of
                Nothing -> ans
                Just vkeyWit -> Set.insert vkeyWit ans
```
