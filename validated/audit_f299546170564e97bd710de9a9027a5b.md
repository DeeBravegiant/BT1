Audit Report

## Title
First Depositor rsETH Price Inflation Attack via Direct ETH Donation to LRTDepositPool - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

## Summary
`LRTDepositPool` has an open `receive()` function whose balance is directly counted as protocol TVL in `getETHDistributionData()`. Because `pricePercentageLimit` is never set in `LRTOracle.initialize()` and defaults to `0`, the public `updateRSETHPrice()` function imposes no cap on price increases. A first depositor can deposit 1 wei, donate a large ETH amount, call `updateRSETHPrice()` to commit the inflated price, and cause every subsequent depositor who passes `minRSETHAmountExpected = 0` to receive zero rsETH while the attacker's single wei of rsETH absorbs the entire pool.

## Finding Description

**Open receive function counted as TVL.**
`LRTDepositPool.receive()` accepts arbitrary ETH with no accounting: [1](#0-0) 

`getETHDistributionData()` uses `address(this).balance` directly, so donated ETH is immediately included in protocol TVL: [2](#0-1) 

**Minting formula divides by stored rsETHPrice.** [3](#0-2) 

`divWad` is `x * 1e18 / y` (WadMath.sol L25-27), so `newRsETHPrice = totalETHInProtocol * 1e18 / rsethSupply`. With `rsethSupply = 1` and `totalETHInProtocol = 10_000e18`, `newRsETHPrice ≈ 10_000 * 1e36`. A victim depositing 5_000 ETH then receives `(5_000e18 * 1e18) / (10_000 * 1e36) = 0` rsETH. [4](#0-3) 

**`pricePercentageLimit` is never initialized.**
`LRTOracle.initialize()` does not set `pricePercentageLimit`, leaving it at the Solidity default of `0`: [5](#0-4) 

The guard is short-circuited when `pricePercentageLimit == 0`: [6](#0-5) 

So `isPriceIncreaseOffLimit` is permanently `false` until an admin explicitly calls `setPricePercentageLimit()`, and `updateRSETHPrice()` is callable by any unprivileged address: [7](#0-6) 

**Fee minting does not block the attack.**
`protocolFeeInBPS` is not set in `LRTConfig.initialize()` and defaults to `0`: [8](#0-7) 

With `protocolFeeInBPS == 0`, `protocolFeeInETH = 0`, so the fee-minting branch is skipped and `_checkAndUpdateDailyFeeMintLimit(0)` is called, which does not revert (`0 + 0 > 0` is false): [9](#0-8) 

**Slippage guard is user-controlled.**
The only protection for the victim is `minRSETHAmountExpected`, which is caller-supplied and commonly `0`: [10](#0-9) 

## Impact Explanation
**Critical — direct theft of user funds.** After the attack the attacker's 1 wei of rsETH represents 100% of the rsETH supply. The victim's ETH is absorbed into the pool and redeemable only by the attacker. The stolen amount equals the victim's full deposit minus the attacker's donation cost, making the attack profitable whenever the victim's deposit exceeds the donation.

## Likelihood Explanation
**Medium.** Three conditions must hold simultaneously: (1) `pricePercentageLimit == 0` — true by default at every deployment, requires no special access; (2) `protocolFeeInBPS == 0` — also true by default, no admin action needed; (3) victim passes `minRSETHAmountExpected = 0` — common in direct contract calls, scripts, and front-end integrations that omit slippage. Condition (1) and (2) are always satisfied at launch. Condition (3) is realistic for any well-funded attacker monitoring the mempool for the first depositor window.

## Recommendation
1. **Set `pricePercentageLimit` in `LRTOracle.initialize()`** to a non-zero value (e.g. 1% = `1e16`) so no single `updateRSETHPrice()` call can move the price by more than the configured threshold.
2. **Seed the protocol with an initial rsETH mint** so the rsETH supply is never 1 wei, eliminating the first-depositor window.
3. **Enforce a non-zero `minRSETHAmountExpected`** at the contract level (e.g. `require(minRSETHAmountExpected >= 1)`) to prevent silent zero-rsETH deposits.
4. **Exclude unaccounted ETH** (ETH sent via `receive()` not deposited through `depositETH`) from `totalETHInProtocol` to remove the donation vector entirely.

## Proof of Concept

```
Preconditions:
  - pricePercentageLimit = 0 (default, never set in initialize())
  - protocolFeeInBPS = 0 (default, never set in initialize())
  - ETH is a supported asset with depositLimit > 15_000 ether
  - updateRSETHPrice() called once with rsethSupply=0 → rsETHPrice = 1e18

Step 1 — Attacker deposits 1 wei ETH:
  depositETH{value: 1}(minRSETHAmountExpected=0, referralId="")
  rsethAmountToMint = (1 * 1e18) / 1e18 = 1
  Attacker holds: 1 wei rsETH, rsETH totalSupply = 1

Step 2 — Attacker donates 10_000 ETH directly:
  (bool ok,) = address(lrtDepositPool).call{value: 10_000 ether}("");
  address(lrtDepositPool).balance = 10_000 ether + 1 wei
  rsETH totalSupply still = 1

Step 3 — Attacker calls updateRSETHPrice():
  totalETHInProtocol = 10_000e18 + 1
  protocolFeeInETH = 0 (protocolFeeInBPS == 0)
  newRsETHPrice = (10_000e18 + 1) * 1e18 / 1 ≈ 10_000 * 1e36
  pricePercentageLimit == 0 → isPriceIncreaseOffLimit = false → no revert
  rsETHPrice = 10_000 * 1e36

Step 4 — Victim deposits 5_000 ETH:
  depositETH{value: 5_000 ether}(minRSETHAmountExpected=0, referralId="")
  rsethAmountToMint = (5_000e18 * 1e18) / (10_000 * 1e36) = 0 (integer division)
  minRSETHAmountExpected=0 → 0 < 0 is false → no revert
  Victim receives 0 rsETH; 5_000 ETH absorbed into pool

Step 5 — Attacker redeems 1 wei rsETH:
  Pool holds ≈ 15_000 ETH; rsETH supply = 1 wei
  Attacker recovers ≈ 15_000 ETH
  Net profit ≈ 5_000 ETH (victim's deposit minus attacker's 10_000 ETH donation)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L243-247)
```text
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTConfig.sol (L28-28)
```text
    uint256 public protocolFeeInBPS;
```
