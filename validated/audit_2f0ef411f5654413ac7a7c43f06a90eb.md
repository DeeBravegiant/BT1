### Title
Share Price Inflation via Direct ETH/LST Transfer Inflates `rsETHPrice`, Causing Victim Deposits to Mint Zero rsETH — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

---

### Summary

An unprivileged attacker can inflate the stored `rsETHPrice` in `LRTOracle` by donating ETH or LST tokens directly to `LRTDepositPool` and then calling the public `updateRSETHPrice()`. When `pricePercentageLimit` is zero (the default), there is no cap on the price increase. A subsequent depositor who passes `minRSETHAmountExpected = 0` receives zero rsETH while their ETH is permanently absorbed into the protocol, giving the attacker a disproportionate claim over all deposited funds.

---

### Finding Description

**Step 1 — Inflatable accounting inputs.**

`LRTDepositPool.getETHDistributionData()` measures the deposit-pool's ETH balance as `address(this).balance`: [1](#0-0) 

`LRTDepositPool.getAssetDistributionData()` measures LST balances as `IERC20(asset).balanceOf(address(this))`: [2](#0-1) 

Both values are inflatable by anyone who sends ETH or LST tokens directly to the contract. The deposit pool's `receive()` function accepts arbitrary ETH: [3](#0-2) 

**Step 2 — Public, uncapped price update.**

`LRTOracle.updateRSETHPrice()` is callable by anyone: [4](#0-3) 

Inside `_updateRsETHPrice()`, the new price is:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
``` [5](#0-4) 

The only guard against an unbounded price increase is:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
``` [6](#0-5) 

`pricePercentageLimit` is a storage variable that defaults to **zero** and is only set by an admin call to `setPricePercentageLimit()`. When it is zero the condition short-circuits to `false`, so the price can be inflated by any factor in a single transaction.

**Step 3 — Mint amount uses the stored (inflated) price.**

`getRsETHAmountToMint` divides by the stored `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [7](#0-6) 

**Step 4 — No floor on minted amount.**

`_beforeDeposit` only checks `rsethAmountToMint < minRSETHAmountExpected`. When the caller passes `minRSETHAmountExpected = 0`, a computed value of zero passes the check silently: [8](#0-7) 

`RSETH.mint(to, 0)` also succeeds — there is no zero-amount guard in the mint function: [9](#0-8) 

---

### Impact Explanation

**Critical — direct theft of user funds.**

A victim who deposits ETH (or an LST) with `minRSETHAmountExpected = 0` receives zero rsETH. Their ETH is permanently held by the protocol. The attacker, holding the only non-zero rsETH balance, owns 100 % of the protocol's shares and can redeem all deposited funds (including the victim's) through the withdrawal system.

---

### Likelihood Explanation

**Medium.**

- `pricePercentageLimit` defaults to zero; the admin must explicitly call `setPricePercentageLimit` to enable the guard. Many deployments or early-lifecycle states will have it unset.
- `minAmountToDeposit` also defaults to zero, allowing a 1-wei seed deposit.
- Front-end integrations and direct contract callers frequently pass `minRSETHAmountExpected = 0` or a very small value.
- The attacker needs capital equal to the victim's deposit to force a zero-mint outcome, but the cost is recoverable once the attacker redeems their inflated rsETH position.

---

### Recommendation

1. **Reject zero-mint deposits.** Add `require(rsethAmountToMint > 0, "ZeroMintAmount")` in `_beforeDeposit` (or equivalently in `getRsETHAmountToMint`).
2. **Use internal accounting instead of raw balances.** Track deposited ETH and LST amounts in storage variables (as the SafEth mitigation did) rather than reading `address(this).balance` and `balanceOf`, so direct transfers cannot inflate the price.
3. **Enforce a non-zero `pricePercentageLimit` at initialization.** Do not allow `updateRSETHPrice()` to succeed when `pricePercentageLimit == 0`; require the admin to set it before the oracle is live.
4. **Seed the protocol with a non-trivial initial deposit** (dead-address mint) to make the rsETH supply large enough that a donation cannot meaningfully shift the price.

---

### Proof of Concept

```
Initial state: rsethSupply = 0, rsETHPrice = 1e18 (set by _updateRsETHPrice when supply == 0)
pricePercentageLimit = 0 (default, not yet set by admin)
minAmountToDeposit = 0 (default)

1. Attacker calls depositETH{value: 1 wei}(0, "")
   → rsethAmountToMint = (1 * 1e18) / 1e18 = 1 wei
   → Attacker holds 1 wei rsETH; rsethSupply = 1 wei

2. Attacker sends 1.5 ETH directly to LRTDepositPool (via receive())
   → address(LRTDepositPool).balance = 1 wei + 1.5e18 wei

3. Attacker calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol ≈ 1.5e18 + 1 wei
   → newRsETHPrice = (1.5e18 + 1) * 1e18 / 1 ≈ 1.5e36
   → pricePercentageLimit == 0 → no revert
   → rsETHPrice stored = ~1.5e36

4. Victim calls depositETH{value: 1.5 ETH}(0, "")
   → rsethAmountToMint = (1.5e18 * 1e18) / 1.5e36 = 1e36 / 1.5e36 = 0 (rounds down)
   → minRSETHAmountExpected = 0 → check passes
   → RSETH.mint(victim, 0) → victim receives 0 rsETH
   → Victim's 1.5 ETH is now in the protocol

5. Attacker holds 1 wei rsETH = 100% of rsETH supply
   → Attacker can redeem: 1 wei rsETH * rsETHPrice / 1e18 ≈ 3 ETH
     (attacker's 1 wei deposit + donated 1.5 ETH + victim's 1.5 ETH)
   → Net profit ≈ 1.5 ETH (victim's entire deposit)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L665-668)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```
