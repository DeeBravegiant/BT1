### Title
Immutable Chainlink Aggregator Address in `ChainlinkOracleForRSETHPoolCollateral` Makes the Contract Irrecoverable if the Feed Goes Stale - (File: contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol)

---

### Summary

`ChainlinkOracleForRSETHPoolCollateral` stores the Chainlink aggregator address as `immutable`. If the aggregator enters a stale state where `answeredInRound < roundID`, `getRate()` permanently reverts with `StalePrice()`. There is no admin function to replace the aggregator, making the contract irrecoverable and freezing all pool deposits that depend on it.

---

### Finding Description

`ChainlinkOracleForRSETHPoolCollateral` wraps a Chainlink aggregator to provide collateral token rates to the RSETHPool family of contracts. The aggregator address is stored as `immutable`:

```solidity
address public immutable oracle;

constructor(address _oracle) {
    oracle = _oracle;
}
```

The `getRate()` function enforces a staleness check:

```solidity
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();

    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    ...
}
```

If the Chainlink aggregator becomes stuck in a stale state (e.g., due to a botched upgrade, sequencer downtime, or feed deprecation), the condition `answeredInRound < roundID` will permanently hold true, causing every call to `getRate()` to revert with `StalePrice()`. Because `oracle` is `immutable`, there is no on-chain path to replace it. The contract is permanently broken.

This is the direct analog of the original report: a hardcoded, non-updatable Chainlink dependency with no governance recovery path.

---

### Impact Explanation

Pool contracts across the RSETHPool family call `IOracle(supportedTokenOracle[token]).getRate()` inside `viewSwapRsETHAmountAndFee()`, which is invoked on every `deposit(token, amount, referralId)`. If `ChainlinkOracleForRSETHPoolCollateral` is set as the oracle for a supported collateral token and its underlying Chainlink feed goes stale, every deposit of that token will revert. Users cannot swap that collateral for rsETH.

The pool admin can work around this by deploying a fresh `ChainlinkOracleForRSETHPoolCollateral` and calling `setSupportedTokenOracle()` (TIMELOCK_ROLE), but the broken contract itself has no recovery path. The window between the feed going stale and the admin completing the TIMELOCK-gated replacement constitutes a temporary freeze of user funds for the affected token.

**Impact: Medium — Temporary freezing of funds (token deposits) for any collateral token whose Chainlink feed enters a stale state.**

---

### Likelihood Explanation

Chainlink aggregators rarely become permanently stale, but the risk is non-zero: botched feed upgrades, feed deprecations, or L2 sequencer outages can all cause `answeredInRound < roundID` to hold persistently. The original finding's judge noted: *"The risk exists, and in general third-party dependencies should be treated with respect in code and documentation."* The same reasoning applies here.

**Likelihood: Low.**

---

### Recommendation

Remove the `immutable` qualifier from `oracle` and add a privileged setter function (ideally behind a timelock) to allow replacing the aggregator address:

```solidity
address public oracle;
address public admin;

function setOracle(address _newOracle) external onlyAdmin {
    require(_newOracle != address(0));
    oracle = _newOracle;
}
```

Alternatively, add a fallback oracle mechanism so that if the primary Chainlink feed reverts, a secondary source is consulted before the contract becomes fully non-functional.

---

### Proof of Concept

1. `ChainlinkOracleForRSETHPoolCollateral` is deployed with a Chainlink aggregator address fixed as `immutable oracle`.
2. The Chainlink aggregator enters a stale state: `answeredInRound < roundID` holds for all subsequent rounds.
3. Any call to `getRate()` hits `if (answeredInRound < roundID) revert StalePrice()` and reverts.
4. In `RSETHPool.viewSwapRsETHAmountAndFee(amount, token)`, the line `uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate()` reverts.
5. `deposit(token, amount, referralId)` reverts for every caller — deposits of the affected collateral token are frozen.
6. There is no function in `ChainlinkOracleForRSETHPoolCollateral` to update `oracle`; the contract is irrecoverable without deploying a replacement and executing a TIMELOCK-gated `setSupportedTokenOracle()` call.

**Root cause:** [1](#0-0) 

**Stale check that permanently reverts:** [2](#0-1) 

**Pool call path that freezes deposits:** [3](#0-2) 

**Pool update mechanism (requires TIMELOCK_ROLE, not in the broken contract):** [4](#0-3)

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L16-16)
```text
    address public immutable oracle;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-36)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

**File:** contracts/pools/RSETHPool.sol (L326-347)
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
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L709-723)
```text
    function setSupportedTokenOracle(
        address token,
        address oracle
    )
        external
        onlyRole(TIMELOCK_ROLE)
        onlySupportedToken(token)
    {
        UtilLib.checkNonZeroAddress(oracle);
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenOracle[token] = oracle;
        emit TokenOracleSet(token, oracle);
    }
```
