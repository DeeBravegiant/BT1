### Title
Token Decimal Mis-Accounting in `viewSwapRsETHAmountAndFee` Causes Severe wrsETH Under-Minting for Non-18-Decimal Collateral - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
`RSETHPoolV3.viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes the wrsETH output by multiplying the raw token-unit amount directly against a WAD-scaled (1e18) oracle rate, without first normalising the token amount to 18 decimals. For any supported token whose decimals differ from 18, the minted wrsETH amount is off by a factor of `10**(18 − tokenDecimals)`, causing depositors to receive a tiny fraction of the wrsETH they are owed while the full token value is locked in the pool.

### Finding Description
The ETH-only overload of `viewSwapRsETHAmountAndFee` correctly normalises the ETH amount:

```solidity
// contracts/pools/RSETHPoolV3.sol L307
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The `1e18` in the numerator compensates for the fact that `rsETHToETHrate` is WAD-scaled; because ETH itself has 18 decimals, `amountAfterFee` is already in the same scale and the result is correct.

The token overload omits this normalisation:

```solidity
// contracts/pools/RSETHPoolV3.sol L334
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

Here `amountAfterFee` is in the token's native decimal scale (e.g. 6 for USDC), while both `tokenToETHRate` and `rsETHToETHrate` are WAD-scaled (1e18). The division cancels the two WAD factors, leaving the result in the token's native decimal scale instead of 1e18. For a 6-decimal token the output is `10**12` times smaller than the correct wrsETH amount.

`ChainlinkOracleForRSETHPoolCollateral.getRate()` correctly normalises the Chainlink answer to 1e18 regardless of feed decimals:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L34
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

So `tokenToETHRate` is always 1e18-scaled, confirming the bug is in the pool arithmetic, not the oracle.

The same pattern propagates into `deposit(address token, uint256 amount, string referralId)` which calls `viewSwapRsETHAmountAndFee` and then mints the returned (incorrect) amount to the user.

### Impact Explanation
A depositor sending 1 000 USDC (6 decimals, worth ~1 000 ETH at a hypothetical 1:1 rate) would receive:

```
rsETHAmount = 1_000e6 * 1e18 / 1e18 = 1_000e6   (≈ 1e9 wrsETH)
```

instead of the correct:

```
1_000 * 1e18 = 1e21 wrsETH
```

The depositor loses `1e12 − 1` times their entitled wrsETH. The full USDC value is retained by the pool, permanently enriching existing wrsETH holders at the depositor's expense. This constitutes direct theft of user funds.

**Impact: Critical — direct theft of depositor funds.**

### Likelihood Explanation
`addSupportedToken` is gated by `TIMELOCK_ROLE`:

```solidity
// contracts/pools/RSETHPoolV3.sol L541
function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
```

The vulnerability is triggered the moment any non-18-decimal token (e.g. USDC, USDT, WBTC) is added as a supported collateral. The pool is explicitly designed to accept arbitrary ERC-20 collateral alongside ETH, and the `addSupportedToken` / `setSupportedTokenOracle` admin surface makes this a realistic operational step. Once such a token is live, any unprivileged depositor calling `deposit(token, amount, referralId)` triggers the loss with no further preconditions.

**Likelihood: Medium** — requires one admin configuration step, after which every depositor of that token is affected.

### Recommendation
Normalise `amountAfterFee` to 18 decimals before the rate division, mirroring the ETH path:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

function viewSwapRsETHAmountAndFee(
    uint256 amount,
    address token
) public view onlySupportedToken(token) returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;

    uint8 tokenDecimals = IERC20Metadata(token).decimals();
    // Normalise to 18 decimals
    uint256 normalizedAmount = amountAfterFee * 1e18 / (10 ** uint256(tokenDecimals));

    uint256 rsETHToETHrate = getRate();
    uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

    rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
}
```

### Proof of Concept
Assume:
- USDC (6 decimals) added as a supported token with a 1:1 ETH oracle (`tokenToETHRate = 1e18`).
- `rsETHToETHrate = 1e18`, `feeBps = 0`.
- User calls `deposit(USDC, 1_000e6, "")`.

**Current behaviour:**
```
amountAfterFee = 1_000e6
rsETHAmount    = 1_000e6 * 1e18 / 1e18 = 1_000e6   // ≈ 1e-12 wrsETH in human units
```

**Expected behaviour:**
```
normalizedAmount = 1_000e6 * 1e18 / 1e6 = 1_000e18
rsETHAmount      = 1_000e18 * 1e18 / 1e18 = 1_000e18  // 1 000 wrsETH
```

The depositor receives `1e12` times fewer wrsETH than owed; the 1 000 USDC remains in the pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
    }
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
