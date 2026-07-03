### Title
Stale `rsETHPrice` Allows Deposit-UpdatePrice-InstantWithdraw Profit Cycle — (`contracts/LRTWithdrawalManager.sol`, `contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a stored value updated only when `updateRSETHPrice()` is called. Because `updateRSETHPrice()` is a public, permissionless function, an attacker can deposit at the stale (lower) price, immediately trigger a price update, and then call `instantWithdrawal` at the freshly updated (higher) price — extracting the accrued yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a state variable that is only refreshed on explicit calls to `updateRSETHPrice()`. [1](#0-0) 

The deposit path in `LRTDepositPool` mints rsETH using the **currently stored** (potentially stale) `rsETHPrice`:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

The `instantWithdrawal` path in `LRTWithdrawalManager` redeems rsETH using the **current** `rsETHPrice` at the moment of the call:

```
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
// = rsETHUnstaked * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)
``` [3](#0-2) [4](#0-3) 

Because `updateRSETHPrice()` is public and permissionless, an attacker controls the exact moment the price is refreshed: [1](#0-0) 

**Attack sequence (single transaction or same block):**

1. `rsETHPrice` is stale at `P_old`; true price (from accrued staking rewards) is `P_new > P_old`.
2. Attacker calls `LRTDepositPool.depositETH(X ETH)` → receives `X / P_old` rsETH (more than fair share at `P_new`).
3. Attacker calls `LRTOracle.updateRSETHPrice()` → `rsETHPrice` becomes `P_new`.
4. Attacker calls `LRTWithdrawalManager.instantWithdrawal(X / P_old rsETH)` → receives `(X / P_old) * P_new * (1 - fee)` ETH.
5. **Net profit ≈ `X * (P_new / P_old - 1)`** ETH, extracted from existing rsETH holders' share of the TVL.

The regular (queued) withdrawal path does **not** exhibit this vulnerability because `_calculatePayoutAmount` caps the payout at `min(expectedAssetAmount, currentReturn)`, and `expectedAssetAmount` is computed at the stale price and equals the deposited amount. [5](#0-4) 

The vulnerability is exclusive to `instantWithdrawal`, which is gated by `isInstantWithdrawalEnabled[asset]`. [6](#0-5) 

---

### Impact Explanation

Each attack cycle extracts `X * (P_new/P_old - 1)` ETH from the protocol's TVL. This value belongs to existing rsETH holders (their principal + accrued yield). The attacker can loop the attack every time rewards accrue and the price drifts. The `pricePercentageLimit` check in `_updateRsETHPrice` limits the maximum price jump per update, but does not prevent the attack — it only bounds the per-cycle profit. [7](#0-6) 

**Impact: Critical — direct theft of user funds from the protocol TVL.**

---

### Likelihood Explanation

- `updateRSETHPrice()` is public and callable by anyone with no access control.
- `instantWithdrawal` is a production feature (the README lists a dedicated `Instant Withdrawal Fee Recipient` address), so `isInstantWithdrawalEnabled` is set to `true` for at least one asset in production.
- The price drifts upward continuously as EigenLayer staking rewards accrue; the attack window opens every time the price has not been updated recently.
- No deposit fee exists in `LRTDepositPool`, so there is zero cost to the attacker beyond gas. [8](#0-7) 

---

### Recommendation

1. **Force a price update before minting**: Call `updateRSETHPrice()` at the start of `depositAsset` / `depositETH` so the deposit always uses the freshest price.
2. **Alternatively, add a deposit fee**: A deposit fee equal to or greater than the maximum expected price drift between updates would make the attack unprofitable.
3. **Snapshot-based accounting**: Record the rsETHPrice at deposit time and use `min(depositPrice, currentPrice)` when computing rsETH to mint, analogous to the `min` guard already used in `_calculatePayoutAmount` for queued withdrawals.

---

### Proof of Concept

Assume:
- `rsETHPrice` (stored, stale) = `1.00 ETH` per rsETH
- True price after accrued rewards = `1.01 ETH` per rsETH
- Attacker deposits `100 ETH`; `instantWithdrawalFee` = 10 bps (0.1%)

**Step 1 — Deposit at stale price:**
```
rsETH minted = 100 ETH / 1.00 = 100 rsETH
```

**Step 2 — Call `updateRSETHPrice()`:**
```
rsETHPrice → 1.01 ETH
```

**Step 3 — `instantWithdrawal` with 100 rsETH:**
```
assetAmountUnlocked = 100 * 1.01 = 101 ETH
fee = 101 * 0.001 = 0.101 ETH
userAmount = 101 - 0.101 = 100.899 ETH
```

**Net profit = 100.899 − 100 = 0.899 ETH** extracted from existing rsETH holders in a single atomic sequence.

The attack is repeatable every time rewards accrue and the price has not been updated, and scales linearly with deposit size.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTDepositPool.sol (L100-118)
```text
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L78-80)
```text
    modifier onlyInstantWithdrawalAllowed(address asset) {
        if (!isInstantWithdrawalEnabled[asset]) revert InstantWithdrawalNotEnabled();
        _;
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
