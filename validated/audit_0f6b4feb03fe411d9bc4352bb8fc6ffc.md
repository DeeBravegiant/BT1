### Title
Ignored Token Decimal Precision in `viewSwapRsETHAmountAndFee` Causes Severely Incorrect rsETH Minting for Non-18-Decimal Tokens - (File: contracts/pools/RSETHPoolV3.sol)

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function present across all L2 pool variants computes the rsETH output amount without normalizing the deposited token amount to 18 decimals. The oracle `getRate()` returns the price of **one whole token** in ETH (18-decimal precision), but `amount` is expressed in the token's native smallest unit. For any ERC-20 with fewer than 18 decimals, the formula silently produces a result that is `10^(18 − tokenDecimals)` times too small.

### Finding Description

Every L2 pool contract that accepts ERC-20 token deposits uses the following formula:

```solidity
// contracts/pools/RSETHPoolV3.sol  lines 324-334
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;

uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

The oracle adapter `ChainlinkOracleForRSETHPoolCollateral.getRate()` normalises the Chainlink answer to 18-decimal precision and returns the price of **one whole token** in ETH:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol  line 34
uint256 normalizedPrice = uint256(ethPrice) * 1e18
    / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

`decimals()` here is the Chainlink feed's own precision (typically 8), **not** the deposited token's decimal count. The result is therefore the price of 1 whole token, expressed with 18-decimal precision.

For an 18-decimal token (e.g. stETH), `amount = 1e18` for one token, so the implicit `1e18` in the numerator and denominator cancel correctly. For a 6-decimal token (e.g. USDC), `amount = 1e6` for one token, but `tokenToETHRate` still represents the price of one whole USDC. The formula is therefore dimensionally inconsistent:

```
rsETHAmount = (1e6 USDC-units) × (3e14 wei-ETH / whole-USDC) / (1.05e18 wei-ETH / rsETH)
            = 285 wei-rsETH
```

Correct value:
```
1 USDC = 0.0003 ETH / 1.05 ETH·rsETH⁻¹ = 0.000285714 rsETH = 285_714_285_714 wei-rsETH
```

The error factor is exactly `10^(18 − 6) = 1e12`. The same formula is replicated without modification in:

- `contracts/pools/RSETHPool.sol` (lines 335–346)
- `contracts/pools/RSETHPoolV3ExternalBridge.sol` (lines 442–452)
- `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` (lines 360–370)

### Impact Explanation

Any depositor who sends a supported ERC-20 token with fewer than 18 decimals receives `10^(18 − tokenDecimals)` times less wrsETH than the deposited value warrants. For USDC (6 decimals) the shortfall is a factor of one trillion. The deposited tokens remain locked in the pool; the user holds a negligible wrsETH balance with no mechanism to recover the difference. The protocol retains the full token value while issuing essentially zero rsETH, constituting a permanent failure to deliver the promised return.

**Impact level: Low** — contract fails to deliver promised returns; the protocol does not lose value, but the depositor does not receive the rsETH they are owed.

### Likelihood Explanation

The vulnerability is latent: it activates the moment any admin adds a supported token whose `decimals()` is not 18. The `supportedTokenOracle` mapping imposes no decimal restriction. The current live tokens (stETH, wstETH) are all 18-decimal, so no user is harmed today. However, the protocol's multi-chain expansion and generic token-support architecture make it plausible that a non-18-decimal collateral (e.g. USDC, USDT, WBTC) is added in a future upgrade without the decimal mismatch being noticed, because the formula appears correct for the 18-decimal case.

**Likelihood: Low** — requires an admin to add a non-18-decimal token, which is a legitimate and foreseeable protocol action.

### Recommendation

Normalise `amountAfterFee` to 18 decimals before applying the rate ratio:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * 1e18 / 10 ** tokenDecimals;
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply the same fix to all four pool contracts that share this formula. Additionally, add a decimal check in the token-registration function to revert if a token with non-18 decimals is added, until the formula is confirmed correct for that case.

### Proof of Concept

**Setup:** Admin calls `addSupportedToken(USDC, chainlinkUSDCOracle)` on `RSETHPoolV3`. USDC has 6 decimals; the oracle returns `3e14` (0.0003 ETH per USDC). rsETH/ETH rate is `1.05e18`.

**Attacker/user action:** Call `deposit(USDC, 1000e6, "")` — depositing 1,000 USDC.

**Expected rsETH minted:**
```
1000 USDC × 0.0003 ETH/USDC / 1.05 ETH/rsETH = 0.2857 rsETH = 285_714_285_714_285 wei
```

**Actual rsETH minted (current code):**
```solidity
// RSETHPoolV3.sol line 334
rsETHAmount = 1000e6 * 3e14 / 1.05e18
            = 3e23 / 1.05e18
            = 285_714  // wei — roughly $0.000000000001
```

The user loses ~$1,000 of USDC and receives wrsETH worth effectively zero. The 1,000 USDC remains in the pool with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-453)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L351-371)
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
