Audit Report

## Title
Token Decimal Mismatch in `viewSwapRsETHAmountAndFee` Causes Near-Total Loss of Depositor Funds for Sub-18-Decimal Tokens - (File: contracts/pools/RSETHPool.sol, RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolNoWrapper.sol)

## Summary

The ERC-20 token overload of `viewSwapRsETHAmountAndFee` computes rsETH output as `amountAfterFee * tokenToETHRate / rsETHToETHrate`, where both rates are 1e18-scaled but `amountAfterFee` is in the token's native decimals. For any token with fewer than 18 decimals, the result is `10^(18 - tokenDecimals)` times too small. A depositor of 1 WBTC (8 decimals) receives ~2857 wei of rsETH instead of ~28.57e18, losing their full principal with no recourse.

## Finding Description

The ETH deposit path in `RSETHPool.sol` correctly normalises the input amount to 1e18 before dividing by the rsETH rate:

```solidity
// RSETHPool.sol line 319 — ETH path (correct)
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The token deposit path omits this normalisation entirely:

```solidity
// RSETHPool.sol line 346 — token path (buggy)
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`tokenToETHRate` is returned by `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which always normalises its output to 1e18 precision regardless of the Chainlink feed's native decimals. [1](#0-0)  `rsETHToETHrate` is likewise 1e18-scaled. [2](#0-1) 

When `amountAfterFee` is in 18-decimal units the formula is accidentally correct because the 1e18 factors cancel. When `amountAfterFee` is in `d < 18` decimal units, the numerator is `10^(18-d)` times too small, producing a proportionally tiny rsETH output.

The `deposit` function transfers the full token amount from the user before calling `viewSwapRsETHAmountAndFee`, then immediately transfers the (near-zero) rsETH amount to the user. [3](#0-2)  There is no minimum-output guard, slippage check, or decimal validation anywhere in the deposit flow.

`addSupportedToken` validates only that the oracle returns a non-zero rate; it performs no check on token decimals. [4](#0-3) 

The identical bug is present in all five pool variants:
- `RSETHPool.sol` line 346 [5](#0-4) 
- `RSETHPoolNoWrapper.sol` line 311 [6](#0-5) 
- `RSETHPoolV3.sol` line 334 [7](#0-6) 
- `RSETHPoolV3ExternalBridge.sol` line 452 [8](#0-7) 
- `RSETHPoolV3WithNativeChainBridge.sol` line 370 [9](#0-8) 

A symmetric error exists in `viewSwapAssetToPremintedRsETH` in `RSETHPoolV3WithNativeChainBridge.sol`, which computes `tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate` without scaling the result back to token decimals. [10](#0-9) 

## Impact Explanation

**Critical — direct theft of user funds.**

For WBTC (8 decimals) with `tokenToETHRate = 30e18` and `rsETHToETHrate = 1.05e18`, depositing 1 WBTC (1e8 units) yields `1e8 * 30e18 / 1.05e18 ≈ 2857` wei of rsETH instead of the correct `≈ 28.57e18`. The depositor's full WBTC balance is transferred into the pool and subsequently bridged to L1; the user receives rsETH worth effectively $0. The loss is permanent and irrecoverable by the user. This matches the **Critical: Direct theft of any user funds** impact class.

## Likelihood Explanation

The vulnerability is latent while only 18-decimal tokens (wstETH, ETH) are supported, but activates immediately upon listing any sub-18-decimal token via `addSupportedToken`. That function is gated by `TIMELOCK_ROLE` but is a routine, intended protocol-extension operation — no malicious admin intent is required. Common and explicitly anticipated collateral candidates (WBTC at 8 decimals, cbBTC at 8 decimals, USDC/USDT at 6 decimals) all have fewer than 18 decimals. Once such a token is listed, every ordinary depositor calling `deposit(token, amount, referralId)` is immediately and automatically affected with no attacker action required.

## Recommendation

Normalise `amountAfterFee` to 1e18 before applying the rate ratio, mirroring the ETH path:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
rsETHAmount = amountAfterFee * tokenToETHRate * 1e18
              / (rsETHToETHrate * 10**uint256(tokenDecimals));
```

Apply the symmetric fix to `viewSwapAssetToPremintedRsETH` in `RSETHPoolV3WithNativeChainBridge.sol`:

```solidity
uint8 tokenDecimals = IERC20Metadata(token).decimals();
tokenAmount = rsETHAmount * rsETHToETHrate * 10**uint256(tokenDecimals)
              / (tokenToETHRate * 1e18);
```

Optionally, add a decimal guard in `addSupportedToken` (e.g., `require(IERC20Metadata(token).decimals() == 18)`) as a defence-in-depth measure until all pool variants are patched.

## Proof of Concept

1. Admin calls `addSupportedToken(WBTC, wbtcOracle, wbtcBridge)` on `RSETHPool` (Arbitrum). `wbtcOracle` is a `ChainlinkOracleForRSETHPoolCollateral` wrapping the BTC/ETH Chainlink feed, returning `30e18`.
2. User calls `deposit(WBTC, 1e8, "ref")` (1 WBTC).
3. `safeTransferFrom` moves `1e8` WBTC units from user to pool. [11](#0-10) 
4. `viewSwapRsETHAmountAndFee(1e8, WBTC)` computes:
   - `fee = 0` (0 bps)
   - `amountAfterFee = 1e8`
   - `tokenToETHRate = 30e18`
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1e8 * 30e18 / 1.05e18 = 2857` [5](#0-4) 
5. Pool transfers `2857` wei of wrsETH to user (≈ $0). [12](#0-11) 
6. User has lost ~$60,000 of WBTC; the WBTC remains in the pool until `moveAssetsForBridging` sends it to L1.

**Foundry test plan:**
```solidity
function testWBTCDecimalMismatch() public {
    // fork Arbitrum, deploy mock WBTC (8 decimals) + oracle returning 30e18
    // addSupportedToken(wbtc, oracle, bridge)
    // deal(wbtc, user, 1e8); approve pool
    // vm.prank(user); pool.deposit(wbtc, 1e8, "");
    // assertGt(wrsETH.balanceOf(user), 28e18); // fails: actual ~2857
}
```

### Citations

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L34-34)
```text
        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/pools/RSETHPool.sol (L296-302)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPool.sol (L339-347)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L637-655)
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
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L310-312)
```text
        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L333-335)
```text
        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L451-453)
```text
        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L369-371)
```text
        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L433-434)
```text
        // Calculate the amount of token user will get for the amount of rsETH
        tokenAmount = rsETHAmount * rsETHToETHrate / tokenToETHRate;
```
