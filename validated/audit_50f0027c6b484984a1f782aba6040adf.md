Let me look at the exact line numbers for the key code sections I need to cite.

### Title
Unsolicited `directDeposits` Can Permanently Freeze Stake Key Deposit via Non-Zero Balance Guard - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in the transaction body that allows any transaction sender to add arbitrary amounts to any registered staking account's balance, with no witness or consent required from the target account. The `ConwayUnRegCert` (and `ShelleyUnRegCert`) processing enforces a strict zero-balance guard before allowing stake key unregistration. An unprivileged attacker can exploit the combination of these two facts to permanently freeze a victim's stake key deposit by continuously keeping the victim's account balance non-zero via unsolicited direct deposits, preventing the victim from ever recovering their deposit.

---

### Finding Description

**Root cause 1 — Permissionless direct deposits**

`dijkstraEntitiesTransition` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs` reads the `directDepositsTxBodyL` field from the transaction body and applies it to the ledger state:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

The only validation performed is `directDepositsMissingAccounts`, which checks that every target credential is a registered account. There is no check that the sender has any authorization from the target account. Any transaction sender can add any amount to any registered account's balance.

`applyDirectDeposits` in `libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs` unconditionally adds the deposited coin to the target account's `balanceAccountStateL`:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

**Root cause 2 — Strict zero-balance guard on unregistration**

`conwayDelegTransition` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs` (used by the Dijkstra era's `CERTS` sub-rule) enforces that the account balance is exactly zero before allowing `ConwayUnRegCert` to proceed:

```haskell
checkStakeKeyHasZeroRewardBalance = do
  accountState <- mAccountState
  let balanceCompact = accountState ^. balanceAccountStateL
  guard (balanceCompact /= mempty)
  Just $ fromCompact balanceCompact
failOnJust
  checkStakeKeyHasZeroRewardBalance
  (injectFailure . StakeKeyHasNonZeroAccountBalanceDELEG)
```

The same guard exists in `delegationTransition` in `eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Deleg.hs` for `UnRegTxCert`.

**Why the victim cannot escape**

The Dijkstra ENTITIES rule processes operations in this order:
1. Validate and apply withdrawals (subtract from balance)
2. Process certificates (unregistration checks balance == 0)
3. Apply direct deposits (add to balance)

A victim can include a withdrawal alongside the unregistration cert in the same transaction. However, the withdrawal amount must be declared in the transaction body at construction time and must satisfy `withdrawalAmount <= balance` at processing time. An attacker who front-runs with a direct deposit of `victimWithdrawal + 1` lovelace ensures that after the victim's withdrawal is applied, the balance is still 1 lovelace, causing the unregistration to fail. The attacker can repeat this indefinitely at a cost of 1 lovelace plus transaction fees per attempt.

---

### Impact Explanation

**High — Permanent freezing of the victim's stake key deposit.**

The victim's stake key deposit (e.g., 2 ADA at current protocol parameters) is locked in the ledger. The victim cannot unregister their stake key and cannot recover the deposit. Recovery would require either a protocol change (hard fork) to remove or relax the zero-balance guard, or a protocol change to require authorization for direct deposits. This matches the allowed impact: *"Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork."*

---

### Likelihood Explanation

**Medium.** The attacker must monitor the mempool for the victim's unregistration transaction and front-run it with a direct deposit. The cost per attack is 1 lovelace plus transaction fees — negligible. The attack can be automated. The victim has no reliable countermeasure: they cannot construct a transaction that atomically drains an unknown future balance and unregisters in the same step, because the withdrawal amount is fixed at construction time and the attacker can always exceed it.

---

### Recommendation

1. **Require a witness from the target account for direct deposits.** Add a validation step in `dijkstraEntitiesTransition` (and `dijkstraSubEntitiesTransition`) that checks each target `AccountAddress` in `directDeposits` is witnessed by the transaction, analogous to how withdrawals require a stake key witness. This prevents unsolicited deposits.

2. **Alternatively, relax the zero-balance guard for unregistration in the Dijkstra era.** Allow `ConwayUnRegCert` to proceed even when the balance is non-zero, automatically draining the balance to the outputs as part of the unregistration. This removes the griefing surface entirely.

---

### Proof of Concept

1. Alice registers a stake key and pays a 2 ADA deposit. Her account balance is 0.
2. Alice constructs `tx_unreg` containing `UnRegDepositTxCert aliceCred (Coin 2_000_000)` and broadcasts it.
3. Bob (attacker) observes `tx_unreg` in the mempool. Bob constructs and submits `tx_attack` containing `directDeposits = {aliceAccountAddress: Coin 1}` with a higher fee to ensure ordering before `tx_unreg`.
4. `tx_attack` is processed first. Alice's balance becomes 1 lovelace.
5. `tx_unreg` is processed. `checkStakeKeyHasZeroRewardBalance` fires: `balanceCompact /= mempty` → `failBecause (StakeKeyHasNonZeroAccountBalanceDELEG (Coin 1))`. Alice's unregistration is rejected.
6. Alice constructs `tx_unreg2` with a withdrawal of 1 lovelace and the unregistration cert. Bob front-runs with `directDeposits = {aliceAccountAddress: Coin 2}`. After Alice's withdrawal of 1, balance is 1 (not 0). Unregistration fails again.
7. Alice's 2 ADA deposit is permanently frozen as long as Bob continues the attack.

**Key files and lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L290-298)
```haskell
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L329-343)
```haskell
directDepositsMissingAccounts ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Maybe DirectDeposits
directDepositsMissingAccounts (DirectDeposits dds) accounts
  | Map.foldrWithKey' checkRegistered True dds = Nothing
  | otherwise = Just $ DirectDeposits $ Map.foldrWithKey' collectMissing Map.empty dds
  where
    isRegistered (AccountAddress _ (AccountId credential)) =
      isAccountRegistered credential accounts
    checkRegistered addr _ acc = acc && isRegistered addr
    collectMissing addr amount acc
      | isRegistered addr = acc
      | otherwise = Map.insert addr amount acc
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L260-268)
```haskell
          checkStakeKeyHasZeroRewardBalance = do
            accountState <- mAccountState
            let balanceCompact = accountState ^. balanceAccountStateL
            guard (balanceCompact /= mempty)
            Just $ fromCompact balanceCompact
      failOnJust checkInvalidRefund id
      failOnJust
        checkStakeKeyHasZeroRewardBalance
        (injectFailure . StakeKeyHasNonZeroAccountBalanceDELEG)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Deleg.hs (L272-277)
```haskell
          checkStakeKeyHasZeroRewardBalance = do
            accountState <- mAccountState
            let accountBalance = accountState ^. balanceAccountStateL
            guard (accountBalance /= mempty)
            Just $ fromCompact accountBalance
      failOnJust checkStakeKeyHasZeroRewardBalance StakeKeyNonZeroAccountBalanceDELEG
```
