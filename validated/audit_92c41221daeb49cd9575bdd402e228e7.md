### Title
Zero rsETH Minted for Dust Deposits Due to Integer Division Truncation - (`contracts/LRTDepositPool.sol`)

---

### Summary

`getRsETHAmountToMint` uses integer division that can truncate to zero for very small deposits. `_beforeDeposit` only reverts if `rsethAmountToMint < minRSETHAmountExpected`, so a caller passing `minRSETHAmountExpected = 0` bypasses the guard entirely, allowing the asset transfer to succeed while minting zero rsETH.

---

### Finding Description

**Root cause — `getRsETHAmountToMint`:** [1](#0-0) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

When `amount * assetPrice < rsETHPrice`, Solidity integer division truncates the result to `0`.

**Guard failure — `_beforeDeposit`:** [2](#0-1) 

The only amount-related checks are:
1. `depositAmount == 0 || depositAmount < minAmountToDeposit` — passes for any non-zero deposit when `minAmountToDeposit` is `0` (its default, never set in `initialize`).
2. `rsethAmountToMint < minRSETHAmountExpected` — evaluates to `0 < 0 = false` when the caller passes `minRSETHAmountExpected = 0`. No revert.

**Execution continues — `depositAsset`:** [3](#0-2) 

The asset is transferred in and `_mintRsETH(0)` is called. OpenZeppelin's `_mint(to, 0)` is a no-op that succeeds silently, so the depositor receives zero rsETH while the protocol retains the collateral.

**`RSETH.mint` with amount = 0:** [4](#0-3) 

The `checkDailyMintLimit` modifier evaluates `currentPeriodMintedAmount + 0 > maxMintAmountPerDay`, which is false for any valid `maxMintAmountPerDay`, so minting 0 always passes.

---

### Impact Explanation

A depositor who sends a dust amount (e.g., 1 wei of stETH) with `minRSETHAmountExpected = 0` receives zero rsETH. The protocol retains the deposited collateral with no corresponding share issued. The depositor permanently loses their yield entitlement on the deposited amount. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value** (the protocol's TVL is unaffected; only the depositor's share is lost).

---

### Likelihood Explanation

The precondition `rsETHPrice > assetPrice * depositAmount` is met as soon as any yield accrues (rsETHPrice rises above `1e18`), which is the normal operating state of the protocol. The `minAmountToDeposit` field is initialized to `0` and never set in `initialize()`: [5](#0-4) 

A user can accidentally trigger this by depositing a dust amount without setting a slippage guard (`minRSETHAmountExpected = 0`). No privilege, oracle manipulation, or admin compromise is required.

---

### Recommendation

Add an explicit zero-mint guard in `_beforeDeposit`:

```solidity
if (rsethAmountToMint == 0) {
    revert InvalidAmountToDeposit();
}
```

Additionally, set a non-zero `minAmountToDeposit` during `initialize` to prevent dust deposits that are economically meaningless.

---

### Proof of Concept

```solidity
// Precondition: rsETHPrice = 1.01e18 (1% yield accrued), stETH assetPrice = 1e18
// depositAmount = 1 wei, minRSETHAmountExpected = 0

// getRsETHAmountToMint:
// rsethAmountToMint = (1 * 1e18) / 1.01e18 = 0  (integer truncation)

// _beforeDeposit:
// depositAmount == 0? No (1 != 0)
// depositAmount < minAmountToDeposit? No (1 < 0 is false, minAmountToDeposit defaults to 0)
// rsethAmountToMint < minRSETHAmountExpected? 0 < 0? No → no revert

// depositAsset continues:
// safeTransferFrom(msg.sender, pool, 1)  ← 1 wei stETH transferred in
// _mintRsETH(0)                          ← 0 rsETH minted
// depositor receives nothing
```

### Citations

**File:** contracts/LRTDepositPool.sol (L45-52)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTDepositPool.sol (L111-116)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L657-669)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/RSETH.sol (L50-55)
```text
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
```
