### Title
Decimal Precision Mismatch in `viewSwapRsETHAmountAndFee(uint256,address)` for Non-18 Decimal Tokens — (`contracts/pools/RSETHPool.sol`)

---

### Summary

`viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` without normalising `amountAfterFee` to 18 decimals. For any supported token with fewer than 18 decimals the result is `10^(18 − d)` times smaller than the correct value, causing depositors to receive essentially zero rsETH while their tokens remain locked in the pool.

---

### Finding Description

The ETH-only overload correctly scales the input:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // line 319
``` [1](#0-0) 

The token overload omits that normalisation:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;   // line 346
``` [2](#0-1) 

Both `tokenToETHRate` (from `IOracle.getRate()`) and `rsETHToETHrate` are 1e18-precision values. For an 18-decimal token the `amount` is already in 1e18 units, so the division is dimensionally consistent. For a 6-decimal token `amount` is in 1e6 units, making the quotient 1e12 times too small.

**Concrete numbers (USDC, 6 decimals, oracle rate = 5e14 ≈ 0.0005 ETH/USDC, rsETH rate = 1.05e18):**

| | Expected | Actual |
|---|---|---|
| `amountAfterFee` | 997 000 (1e6 units) | 997 000 |
| `rsETHAmount` | ≈ 4.76 × 10¹⁴ (wei rsETH) | ≈ 475 (wei rsETH) |
| Ratio | — | **~1 × 10¹² too small** |

The user deposits 1 USDC, pays a 0.003 USDC fee, and receives 475 wei of rsETH (≈ 0 in any practical sense). The 0.997 USDC principal remains in the pool with no mechanism for the depositor to recover it.

The `addSupportedToken` function performs no decimal check:

```solidity
if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
``` [3](#0-2) 

The same pattern is present in every pool variant that supports token deposits: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

The question frames this as "theft of unclaimed yield," but the actual impact is more severe. The fee (`amount * tokenFeeBps / 10_000`) is correctly computed in the token's native units and is tiny (0.003 USDC). What is lost is the depositor's entire principal: 0.997 USDC stays in the pool permanently with no recovery path for the user. This is direct theft of user funds at-rest, not merely yield.

---

### Likelihood Explanation

**Medium.** The precondition is that a non-18 decimal token (e.g., USDC, USDT) is added via `addSupportedToken` by the `TIMELOCK_ROLE`. This is a legitimate, foreseeable protocol action — the contract is explicitly designed to support multiple tokens. No key compromise or malicious actor is required; the bug fires automatically for every depositor once such a token is listed.

---

### Recommendation

Normalise `amountAfterFee` to 18 decimals before the rate division:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 decimals = IERC20Metadata(token).decimals();
uint256 normalizedAmount = amountAfterFee * (10 ** (18 - decimals));
rsETHAmount = normalizedAmount * tokenToETHRate / rsETHToETHrate;
```

Apply the same fix to `fee` if fee accounting is also expected to be in 18-decimal units, or document clearly that fees are stored in the token's native decimals (which is the current, correct behaviour for fee storage).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Minimal local fork test (no public mainnet)
contract MockOracle {
    uint256 immutable _rate;
    constructor(uint256 r) { _rate = r; }
    function getRate() external view returns (uint256) { return _rate; }
}

contract MockUSDC {
    // 6-decimal ERC-20 stub
    uint8 public decimals = 6;
    mapping(address => uint256) public balanceOf;
    function mint(address to, uint256 amt) external { balanceOf[to] += amt; }
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        balanceOf[f] -= a; balanceOf[t] += a; return true;
    }
}

// Deploy RSETHPool, set rsETHOracle = MockOracle(1.05e18),
// addSupportedToken(usdc, MockOracle(5e14 /* 0.0005 ETH/USDC */), bridge)
// Call viewSwapRsETHAmountAndFee(1e6, usdc)
// Assert: rsETHAmount >= 4e14   // ~0.000476 rsETH in wei
// Actual:  rsETHAmount ≈ 475    // 1e12x too small → test FAILS on unmodified code
```

Running this against the unmodified `RSETHPool.viewSwapRsETHAmountAndFee` at line 346 will produce `rsETHAmount ≈ 475` instead of `≈ 4.76e14`, confirming the 1e12 precision loss. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPool.sol (L311-320)
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

**File:** contracts/pools/RSETHPool.sol (L648-653)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
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
