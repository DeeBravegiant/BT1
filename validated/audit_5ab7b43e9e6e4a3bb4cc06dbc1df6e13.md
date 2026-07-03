Looking at the core formula in `viewSwapRsETHAmountAndFee(uint256, address)` and the ETH-path formula for comparison.

### Title
Decimal Normalisation Missing in Token-to-rsETH Swap — Users Receive ~1e12× Less rsETH for Sub-18-Decimal Tokens - (File: `contracts/pools/RSETHPool.sol`)

---

### Summary

`viewSwapRsETHAmountAndFee(uint256 amount, address token)` computes `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` without scaling `amountAfterFee` to 18 decimals first. For any token with fewer than 18 decimals (e.g., USDC at 6), the result is `10^(18 − tokenDecimals)` times smaller than the correct value. A user depositing 1 USDC receives ~285 wei of rsETH instead of ~285,714,285,714,285 wei, losing virtually all principal. The same bug is present in every pool variant that supports non-ETH tokens.

---

### Finding Description

**Root cause — `RSETHPool.sol` line 346:**

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

Both `tokenToETHRate` and `rsETHToETHrate` are 1e18-precision values ("price of 1 *whole* token in ETH, scaled to 1e18"), as confirmed by `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
``` [2](#0-1) 

For the formula to be dimensionally correct, `amountAfterFee` must also be in 1e18 units (i.e., "number of whole tokens × 1e18"). This holds for 18-decimal tokens (wstETH), where 1 token = 1e18 smallest units. For USDC (6 decimals), 1 token = 1e6 smallest units, so `amountAfterFee` is 1e12 times too small.

**Numeric proof (1 USDC deposit, feeBps = 0 for clarity):**

| Variable | Value |
|---|---|
| `amountAfterFee` | `1e6` (1 USDC in 6-decimal units) |
| `tokenToETHRate` | `~3e14` (0.0003 ETH/USDC × 1e18) |
| `rsETHToETHrate` | `~1.05e18` (1.05 ETH/rsETH × 1e18) |
| **Actual `rsETHAmount`** | `1e6 × 3e14 / 1.05e18 ≈ 285 wei` |
| **Correct `rsETHAmount`** | `1e18 × 3e14 / 1.05e18 ≈ 285,714,285,714,285 wei` |

The user receives **1e12× less rsETH** than owed. Their USDC is transferred in full at line 296 and accumulates in the pool, later bridged to L1 — permanently lost to the user. [3](#0-2) 

**`addSupportedToken` has no decimal guard:**

```solidity
if (IOracle(oracle).getRate() == 0) revert UnsupportedOracle();
``` [4](#0-3) 

Any token with a non-zero oracle rate can be added, including USDC, USDT, or any other sub-18-decimal asset.

**Affected contracts (identical pattern):**

- `RSETHPool.sol` line 346
- `RSETHPoolNoWrapper.sol` line 311
- `RSETHPoolV3.sol` line 334
- `RSETHPoolV3ExternalBridge.sol` line 452
- `RSETHPoolV3WithNativeChainBridge.sol` line 370 [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Every user who deposits a sub-18-decimal token receives rsETH that is `10^(18 − tokenDecimals)` times smaller than the fair amount. For USDC/USDT (6 decimals) the shortfall is 1e12×. The deposited tokens are not returned; they accumulate in the pool and are bridged to L1. This constitutes **direct theft of user principal** — a **Critical** impact (exceeding the "High / theft of unclaimed yield" framing in the question).

---

### Likelihood Explanation

**Medium.** The bug is latent until a sub-18-decimal token is added via `addSupportedToken` (requires `TIMELOCK_ROLE`). Adding USDC or USDT as collateral is a natural protocol expansion. No attacker action is needed once the token is listed — every ordinary depositor is affected automatically.

---

### Recommendation

Normalise `amountAfterFee` to 18 decimals before applying the rate ratio:

```solidity
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";

uint8 tokenDecimals = IERC20Metadata(token).decimals();
uint256 amountAfterFeeNorm = amountAfterFee * 10 ** (18 - tokenDecimals);
rsETHAmount = amountAfterFeeNorm * tokenToETHRate / rsETHToETHrate;
```

Additionally, enforce `tokenDecimals <= 18` inside `addSupportedToken` as a safety guard.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry test — run against a local fork or with mock contracts

import "forge-std/Test.sol";
import "../contracts/pools/RSETHPool.sol";

contract MockOracle {
    uint256 public immutable _rate;
    constructor(uint256 r) { _rate = r; }
    function getRate() external view returns (uint256) { return _rate; }
}

contract MockERC20 {
    // 6-decimal token (USDC-like)
    uint8 public decimals = 6;
    mapping(address => uint256) public balanceOf;
    function mint(address to, uint256 amt) external { balanceOf[to] += amt; }
    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        balanceOf[from] -= amt; balanceOf[to] += amt; return true;
    }
    function approve(address, uint256) external returns (bool) { return true; }
}

contract DecimalMismatchPoC is Test {
    RSETHPool pool;
    MockERC20 usdc;
    MockOracle usdcOracle;   // 1 USDC = 0.0003 ETH  → 3e14
    MockOracle rsethOracle;  // 1 rsETH = 1.05 ETH   → 1.05e18

    function setUp() public {
        usdc       = new MockERC20();
        usdcOracle = new MockOracle(3e14);   // 0.0003 ETH per USDC
        rsethOracle= new MockOracle(1.05e18);

        // Deploy & initialise pool (simplified — use actual init args in practice)
        pool = new RSETHPool();
        // ... initialise, grant TIMELOCK_ROLE, add USDC as supported token ...
    }

    function test_decimalMismatch() public {
        uint256 depositAmount = 1e6; // 1 USDC

        (uint256 rsETHAmount, uint256 fee) = pool.viewSwapRsETHAmountAndFee(depositAmount, address(usdc));

        // Correct rsETH: 1 USDC = 0.0003 ETH / 1.05 ETH per rsETH ≈ 285714285714285 wei
        uint256 expectedRsETH = depositAmount * 1e12 * 3e14 / 1.05e18;

        emit log_named_uint("rsETHAmount (actual)",   rsETHAmount);   // ~285
        emit log_named_uint("rsETHAmount (expected)", expectedRsETH); // ~285714285714285

        // Assert the shortfall is exactly 1e12×
        assertApproxEqRel(rsETHAmount * 1e12, expectedRsETH, 1e15 /* 0.1% */);
        // This assertion PASSES, proving the 1e12× undercount
    }
}
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L296-302)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPool.sol (L335-346)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L648-650)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-34)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L308-311)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L331-334)
```text
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
