### Title
Missing Access Control on `sendFunds()` Allows Anyone to Prematurely Flush MEV Rewards and Manipulate rsETH Exchange Rate - (File: contracts/FeeReceiver.sol)

### Summary
The `sendFunds()` function in `FeeReceiver` lacks any access control, allowing any external address to force all accumulated MEV/execution-layer rewards into the deposit pool at an arbitrary time. This enables an attacker to manipulate the rsETH exchange rate by controlling the exact moment rewards are flushed, enabling sandwich-style attacks against depositors or withdrawers.

### Finding Description
`FeeReceiver` is the contract that accumulates MEV and execution-layer rewards for the protocol. Its `sendFunds()` function is intended to forward the entire ETH balance to `LRTDepositPool`, which increases TVL and thus the rsETH/ETH exchange rate. However, the function carries no role check or modifier of any kind:

```solidity
/// @dev send all rewards to deposit pool
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

Every other state-changing function in the contract that is not a pure receive is gated behind `onlyRole(LRTConstants.MANAGER)`: [2](#0-1) 

The contract inherits `AccessControlUpgradeable` and sets up both `DEFAULT_ADMIN_ROLE` and `LRTConstants.MANAGER` during initialization, so the infrastructure for access control is already present but simply not applied to `sendFunds()`. [3](#0-2) 

### Impact Explanation
MEV rewards accumulate in `FeeReceiver` over time. When `sendFunds()` is called, the entire balance is pushed to `LRTDepositPool.receiveFromRewardReceiver()`, which immediately increases the rsETH/ETH exchange rate for all holders. An attacker can exploit the unguarded trigger to:

1. **Front-run a large deposit**: Call `sendFunds()` in the same block just before a large depositor's transaction. The exchange rate spikes, so the depositor receives fewer rsETH tokens than they would have without the manipulation.
2. **Back-run their own withdrawal**: Accumulate rsETH, then call `sendFunds()` immediately before redeeming, capturing the full reward spike at the expense of other holders who did not yet redeem.

In both cases the attacker extracts value that should have been distributed proportionally over time to all rsETH holders. This constitutes unauthorized manipulation of yield distribution — the analog of the Jigsaw finding where bypassing the `StrategyManager` caused reward inconsistencies.

### Likelihood Explanation
The entry path is fully permissionless: any EOA or contract can call `sendFunds()` with zero preconditions. No special role, no deposit, no prior interaction is required. The attack is profitable whenever a meaningful ETH balance has accumulated in `FeeReceiver`, which happens continuously as validators produce MEV and execution-layer rewards. Likelihood is **High**.

### Recommendation
Add the `onlyRole(LRTConstants.MANAGER)` modifier to `sendFunds()`, consistent with the access pattern used for every other privileged operation in the contract:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

### Proof of Concept
```solidity
function test_anyoneCanFlushMevRewards(address attacker) public {
    // Simulate MEV rewards accumulating in FeeReceiver
    vm.deal(address(feeReceiver), 10 ether);

    uint256 rateBefore = lrtOracle.rsETHPrice();

    // Attacker calls sendFunds() with no role or permission
    vm.prank(attacker);
    feeReceiver.sendFunds();

    uint256 rateAfter = lrtOracle.rsETHPrice();

    // Exchange rate increased — attacker controlled the timing
    assertGt(rateAfter, rateBefore, "Rate should have increased");

    // A depositor who submitted their tx in the same block now receives
    // fewer rsETH tokens than they expected at rateBefore
}
```

### Citations

**File:** contracts/FeeReceiver.sol (L44-47)
```text
        __AccessControl_init();
        _setupRole(DEFAULT_ADMIN_ROLE, admin);
        _setupRole(LRTConstants.MANAGER, manager);
    }
```

**File:** contracts/FeeReceiver.sol (L52-58)
```text
    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/FeeReceiver.sol (L66-72)
```text
    function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
        if (_depositPool == address(0)) revert InvalidEmptyValue();

        depositPool = _depositPool;

        emit DepositPoolSet(_depositPool);
    }
```
