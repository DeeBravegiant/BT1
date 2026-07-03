Looking at the vulnerability class — **rounding to zero in a price/rate calculation causing fund loss or DoS** — I need to find analogous integer-division rounding issues in LRT-rsETH's production contracts.

Let me examine the key rate/amount calculations in the L2 pool deposit paths and the withdrawal manager.