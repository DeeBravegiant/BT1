### Title
Token Type Mismatch in `RsETHTokenWrapper._withdraw` Enables Draining Higher-Value rsETH Variants - (File: contracts/L2/RsETHTokenWrapper.sol)

### Summary
`RsETHTokenWrapper._withdraw` burns a fixed `_amount` of wrsETH and transfers the same `_amount` of any caller-chosen `_asset`, with no validation that the chosen asset is worth the same as the wrsETH burned. Because the wrapper is designed to hold multiple distinct rsETH variants simultaneously, an unprivileged user can deposit the lower-value variant, receive wrsETH 1:1, then withdraw the higher-value variant 1:1, extracting the price difference from the pool.

### Finding Description
`RsETHTokenWrapper` is a multi-token wrapper that accepts several rsETH variants (original rsETH and one or more `altRsETH` tokens added via `reinitialize` / `addAllowedToken`) and mints wrsETH 1: