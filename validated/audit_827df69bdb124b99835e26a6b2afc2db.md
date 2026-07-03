### Title
`setRSETHOracle` Accepts Zero-Rate Oracle, Temporarily Freezing All Deposits - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
All L2 pool contracts expose a `setRSETHOracle` function that accepts any non-zero address as the new rsETH oracle without validating that the oracle currently returns a non-zero rate. If a freshly deployed `CrossChainRateReceiver` (whose `rate` storage slot starts at 0) is set as the rsETH oracle before it receives its first LayerZero rate update, every deposit call reverts with a division-by-zero panic, temporarily freezing all deposits across the pool.

### Finding Description
`CrossChainRateReceiver.rate` is a plain `uint256` storage variable initialized to 0 by default. It is only updated when a LayerZero message is received via `lzReceive()`. [1](#0-0) [2](#0-1) 

`getRate()` simply returns the stored `rate` with no staleness or zero-value guard: [3](#0-2) 

Every pool contract's `setRSETHOracle` only checks that the address is non-zero — it never calls `getRate()` on the candidate oracle: [4](#0-3) [5](#0-4) [6](#0-5) 

This is directly inconsistent with `setSupportedTokenOracle` and `addSupportedToken`, which both guard against a zero rate: [7](#0-6) [8](#0-7) 

When `rsETHToETHrate` is 0, both deposit paths divide by it: [9](#0-8) [10](#0-9) 

Solidity 0.8.x raises a division-by-zero panic, reverting every deposit transaction until the oracle receives its first cross-chain rate message.

The same flaw is present in `RSETHPool`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper`. [11](#0-10) [12](#0-11) 

### Impact Explanation
All ETH and token deposits into the affected pool are temporarily frozen. Users who send ETH receive it back (revert), but the pool is completely non-functional for deposits during the window between oracle deployment and the first LayerZero rate delivery. Depending on cross-chain message latency and operator response time, this window can span minutes to hours. Impact: **Medium — Temporary freezing of funds.**

### Likelihood Explanation
Oracle migration is a routine operational event (e.g., upgrading to a new `CrossChainRateReceiver` or `RSETHRateReceiver`). The admin deploys the new receiver, calls `setRSETHOracle`, and the freeze begins immediately. The rate update arrives only after the L1 provider sends a LayerZero message. No malicious actor is required; a well-intentioned admin following a standard upgrade sequence triggers the freeze. Likelihood: **Medium.**

### Recommendation
Mirror the guard already present in `setSupportedTokenOracle` and `addSupportedToken`:

```solidity
function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(_rsETHOracle);
    if (IOracle(_rsETHOracle).getRate() == 0) {
        revert UnsupportedOracle();
    }
    rsETHOracle = _rsETHOracle;
    emit OracleSet(_rsETHOracle);
}
```

Apply the same fix to `initialize` in every pool contract that sets `rsETHOracle` without a rate check.

### Proof of Concept

1. Admin deploys a new `CrossChainRateReceiver`; its `rate` storage is 0.
2. Admin calls `RSETHPoolV3.setRSETHOracle(newReceiver)`. The call succeeds because the address is non-zero.
3. Before the first LayerZero rate message arrives, Alice calls `deposit{value: 1 ether}("ref")`.
4. `deposit` invokes `viewSwapRsETHAmountAndFee(1 ether)`.
5. `rsETHToETHrate = getRate()` → `CrossChainRateReceiver.rate` → **0**.
6. `rsETHAmount = amountAfterFee * 1e18 / 0` → **division-by-zero panic, revert**.
7. Alice's ETH is returned, but she cannot deposit. Every subsequent depositor hits the same revert until the oracle is updated via LayerZero. [9](#0-8) [13](#0-12)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-105)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L532-537)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L548-550)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L584-586)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L751-756)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L540-547)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        rsETHOracle = _rsETHOracle;

        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPool.sol (L604-611)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);

        rsETHOracle = _rsETHOracle;

        emit OracleSet(_rsETHOracle);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L588-593)
```text
    /// @param _rsETHOracle The rsETHOracle address
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```
