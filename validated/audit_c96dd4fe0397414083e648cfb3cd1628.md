### Title
Stale Reverse DRep Delegation During Bootstrap Phase Causes Permanent Vote Delegation Loss — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version 9), when a stake credential re-delegates its vote from DRep A to DRep B, the old reverse delegation entry in DRep A's `drepDelegs` set is not removed. If DRep A is subsequently unregistered, the system incorrectly clears the stake credential's account-level DRep delegation pointer — because the credential is still listed in DRep A's stale `drepDelegs` — even though the credential had already moved to DRep B. The `updateDRepDelegations` cleanup at the PV10 hard-fork transition rebuilds `drepDelegs` from account states but cannot restore a delegation pointer that was already cleared, so the credential permanently loses its vote delegation in post-bootstrap governance.

---

### Finding Description

**Root cause — `processDelegationInternal`, bootstrap branch:**

In `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`, the `delegVote` helper inside `processDelegationInternal` handles re-delegation of an existing account during the bootstrap phase (`preserveIncorrectDelegation = pvMajor pv < natVersion @10`):

```haskell
| isNothing mAccountState || preserveIncorrectDelegation ->
    certVStateL . vsDRepsL
      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
```

<cite repo="Rodmore11/cardano