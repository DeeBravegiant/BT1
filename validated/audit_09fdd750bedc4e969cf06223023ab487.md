### Title
Unchecked `withdrawETH` Return Value Causes `totalETHDepositedToAave` Desynchronization, Enabling Misclassification of Principal as Interest - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` uses a minimal custom `IWrappedTokenGatewayV3` interface that declares `withdrawETH` with no return value. The internal `_withdrawFromAave` function decrements `totalETHDepositedToAave` by the *requested* amount without verifying the *actual* ETH received. If the actual amount ever diverges from the requested amount (e.g., due to a future Aave gateway upgrade), `totalETHDepositedToAave` becomes permanently desynchronized. A desynchronized tracker causes `_collectInterestToTreasury` to misclassify principal as interest and drain it to the treasury, or causes `_checkAaveHealth` to permanently block interest collection.

### Finding Description

`IWrappedTokenGatewayV3` is a two-function minimal stub:

```solidity
// contracts/interfaces/aave/IWrappedTokenGatewayV3.sol
interface IWrappedTokenGatewayV3 {
    function depositETH(address pool, address onBehalfOf, uint16 referralCode) external payable;
    function withdrawETH(address pool, uint256 amount, address to) external;   // no return value
}
```

<cite repo="Tylerpinwa/LRT-rsETH--005" path="contracts/interfaces/a