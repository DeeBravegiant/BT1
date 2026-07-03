### Title
Missing Time-Based Staleness Check with Immutable Oracle Address — (`contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`)

### Summary
`ChainlinkOracleForRSETHPoolCollateral` performs only a sequence-based staleness check (`answeredInRound < roundID`) and omits any time-based staleness threshold. Because the `oracle` address is declared `immutable`, there is no post-deployment mechanism to introduce a staleness window or swap the feed. Every collateral token routed through this wrapper shares the same absent protection — a direct structural analog to M-11's single, unchangeable `_updatePeriod` applied uniformly to all assets.

### Finding Description
`ChainlinkOracleForRSETHPoolCollateral.getRate()` calls `latestRoundData()` and validates only that `answeredInRound >= roundID` and `timestamp != 0`. [1](#0-0) 

No check of the form `block.timestamp - timestamp > MAX_STALENESS` is present. The `oracle` field is `immutable`: [2](#0-1) 

so neither a staleness threshold nor a replacement feed can be introduced after deployment. `RSETHPool` and `RSETHPoolNoWrapper` register per-token oracles of this type via `addSupportedToken`, and every such token is subject to the same absent time-based guard: [3](#0-2) [4](#0-3) 

The rsETH minting calculation divides the deposit amount by the oracle rate: [5](#0-4) 

A stale (lower-than-actual) collateral price causes the denominator to be understated, minting more rsETH per unit of collateral than the protocol's accounting warrants.

### Impact Explanation
When a Chainlink heartbeat is missed — a known operational event during network congestion — `answeredInRound == roundID` can still hold while `timestamp` is hours old. The pool accepts the stale rate without reverting. Depositors receive an incorrect rsETH amount: if the stale price is below the true price, they receive excess rsETH, diluting existing holders; if above, they receive less than owed. The contract fails to deliver the exchange rate it promises. This maps to **Low — contract fails to deliver promised returns**.

### Likelihood Explanation
Chainlink heartbeat misses are documented and occur on mainnet during periods of high gas prices or network congestion. The pool is publicly callable by any depositor. No privileged access is required to trigger the path; the depositor simply calls `deposit()` during a window when the feed is stale but the round sequence check passes.

### Recommendation
1. Introduce a configurable `maxStaleness` parameter per oracle wrapper (or per token in the pool), settable by a privileged role post-deployment, analogous to the `setUpdatePeriod` fix recommended in M-11.
2. Add a time-based check inside `getRate()`:
   ```solidity
   if (block.timestamp - timestamp > maxStaleness) revert StalePrice();
   ```
3. Replace `immutable oracle` with a mutable, access-controlled field so the feed can be rotated if deprecated.

### Proof of Concept

1. Chainlink's ETH/stETH (or similar) feed misses its heartbeat; `updatedAt` is 4 hours old, but `answeredInRound == roundID`.
2. A depositor calls `RSETHPool.deposit(token, amount, referralId)`.
3. The pool calls `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
4. `answeredInRound < roundID` → false; `timestamp == 0` → false; `ethPrice <= 0` → false. All checks pass.
5. The stale (lower) price is returned; `rsETHAmount = amountAfterFee * 1e18 / staleRate` is inflated.
6. The depositor receives more rsETH than the current true rate warrants, diluting existing holders.
7. No admin action can correct this mid-flight because `oracle` is `immutable` and no staleness window exists to tighten. [1](#0-0)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L16-16)
```text
    address public immutable oracle;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/pools/RSETHPool.sol (L637-656)
```text
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L569-592)
```text
    /// @dev Adds a supported token
    /// @param token The token address
    /// @param oracle The oracle address for the token
    /// @param bridge The bridge address for the token
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
