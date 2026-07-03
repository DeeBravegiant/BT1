### Title
Publicly Callable `sendFunds()` Enables Front-Running of MEV Reward Distribution — (File: contracts/FeeReceiver.sol)

### Summary
`FeeReceiver.sendFunds()` carries no access control and can be called by any external account. Because calling it increases the protocol's TVL (and therefore the rsETH price), an attacker can sandwich the call — depositing ETH immediately before it to acquire rsETH at the pre-reward price, then exiting after the price rises — capturing a disproportionate share of MEV/execution-layer rewards that should accrue to all existing rsETH holders.

### Finding Description
`FeeReceiver` accumulates MEV and execution-layer rewards as plain ETH. The only way to move those rewards into the protocol is `sendFunds()`:

```solidity
// contracts/FeeReceiver.sol
function sendFunds() external {                          // ← no role check
    uint256