Looking at the external report's vulnerability class — **a fee/accounting parameter exists but the corresponding collection is never performed, bypassing the intended protocol accounting** — I need to find an analog in Cardano Ledger where a fee, deposit, or value that should be collected for a protocol pot is silently omitted.

Let me trace the Dijkstra era's sub-transaction accounting path.