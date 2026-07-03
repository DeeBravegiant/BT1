### Title
Silent Zero rsETH Mint When Token Oracle Returns Zero in `viewSwapRsETHAmountAndFee` - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
`viewSwapRsETHAmountAndFee(uint256 amount, address token)` silently returns `(0, fee)` — or `(0, 0)` when `feeBps == 0` — when the token oracle returns 0, instead of reverting. The `deposit()` function consumes this without validation, taking the user's tokens while minting 0 rsETH. The same flaw exists across `RSETHPoolV3`, `RSETHPoolNoWrapper`, and `RSETHPool`.

### Finding Description
In `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee(uint256 amount, address token)`, the function fetches the token oracle rate and uses it directly in the rsETH amount calculation with no zero-check:

```solidity
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
// ...
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

If `tokenToETHRate ==

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L448-452)
```text
        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
