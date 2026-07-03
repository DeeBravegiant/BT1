### Title
Division by Zero in `getRsETHAmountToMint` When `rsETHPrice` Is Uninitialized — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()` without a zero-guard. The `rsETHPrice` storage variable in `LRTOracle` is initialized to `0` by default and is only set to a non-zero value after `updateRSETHPrice()` is called. Any deposit attempt before that call reverts with a division-by-zero panic, temporarily blocking all deposits.

---

### Finding Description

In `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol  line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` reads the public state variable `rsETHPrice` declared in `LRTOracle`:

```solidity
// contracts/LRTOracle.sol  line 28
uint256 public override rsETHPrice;
```

This variable starts at `0` (Solidity default). It is only assigned a non-zero value inside `_updateRsETHPrice()`:

- Set to `1 ether` when `rsethSupply == 0` (lines 218–222)
- Set to `newRsETHPrice` otherwise (line 313)

`_updateRsETHPrice()` is only reachable via `updateRSETHPrice()` (public, `whenNotPaused`) or `updateRSETHPriceAsManager()` (manager-only). Neither is called automatically on deployment or on the first deposit.

`getRsETHAmountToMint()` is called by `_beforeDeposit()` (line 665), which is called by both `depositETH()` (line 87) and `depositAsset()` (line 111). None of these callers check whether `rsETHPrice` is zero before the division. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Any call to `depositETH()` or `depositAsset()` before `updateRSETHPrice()` has ever been executed will revert with a division-by-zero panic. This temporarily freezes all deposit functionality for every user until the price is initialized.

**Impact: Medium — Temporary freezing of funds (deposits).** [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The window exists between contract deployment and the first successful call to `updateRSETHPrice()`. Because `updateRSETHPrice()` is public and callable by anyone, any user can unblock deposits themselves — but the code provides no guarantee or enforcement that this happens before deposits are attempted. The scenario is realistic immediately after a fresh deployment or after an upgrade that resets state. [6](#0-5) 

---

### Recommendation

Add a zero-guard in `getRsETHAmountToMint()` before dividing:

```solidity
uint256 currentRsETHPrice = lrtOracle.rsETHPrice();
if (currentRsETHPrice == 0) revert RsETHPriceNotInitialized();
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / currentRsETHPrice;
```

Alternatively, enforce that `updateRSETHPrice()` is called as part of the initialization sequence (e.g., in `initialize()`), so `rsETHPrice` is never `0` when deposits are open. [7](#0-6) 

---

### Proof of Concept

1. Deploy `LRTOracle` and `LRTDepositPool` (fresh deployment, `rsETHPrice == 0`).
2. Do **not** call `updateRSETHPrice()`.
3. Call `depositETH{value: 1 ether}(0, "")` as any user.
4. Execution reaches `getRsETHAmountToMint()` → line 520 executes `/ lrtOracle.rsETHPrice()` → `/ 0` → EVM panic revert.
5. All deposits are blocked until `updateRSETHPrice()` is called by any account. [8](#0-7) [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
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
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-222)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```
