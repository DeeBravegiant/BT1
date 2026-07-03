### Title
Fee-on-Transfer Token Mis-Accounting Causes rsETH Over-Minting and Protocol Insolvency - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset()` calculates the rsETH amount to mint using the caller-supplied `depositAmount` before the actual token transfer occurs. For fee-on-transfer tokens, the contract receives fewer tokens than `depositAmount`, but mints rsETH as if the full `depositAmount` was received. This inflates the rsETH supply beyond the real asset backing, making the protocol insolvent.

---

### Finding Description

In `LRTDepositPool.depositAsset()`, the rsETH mint amount is computed from the user-supplied `depositAmount` parameter via `_beforeDeposit` → `getRsETHAmountToMint`, and then the token transfer is executed:

```solidity
// contracts/LRTDepositPool.sol:111-115
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);
```

`getRsETHAmountToMint` uses `amount` (the user-specified value) directly:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

For a fee-on-transfer token, `safeTransferFrom(..., depositAmount)` results in the contract receiving only `depositAmount - fee`. However, `rsethAmountToMint` was already fixed to the full `depositAmount` value. The protocol therefore mints rsETH worth more than the assets it actually holds.

The same pattern exists in the L2 pool contracts. For example, `RSETHPoolV3.deposit(address token, ...)`:

```solidity
// contracts/pools/RSETHPoolV3.sol:284-290
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

`viewSwapRsETHAmountAndFee` is called with the original `amount`, not the actual received amount, and `wrsETH.mint` issues tokens based on that inflated figure.

---

### Impact Explanation

Every deposit of a fee-on-transfer token mints rsETH in excess of the real asset value received. The rsETH total supply grows faster than the total assets backing it. When users redeem rsETH, the last redeemers cannot be made whole because the pool holds fewer assets than the outstanding rsETH supply implies. The deficit equals the cumulative transfer fees across all deposits. This is **protocol insolvency** — a Critical impact.

---

### Likelihood Explanation

The protocol uses a generic `onlySupportedERC20Token` modifier with no on-chain guard against fee-on-transfer tokens. Any governance action that adds a fee-on-transfer LST (e.g., a rebasing token with a transfer tax) as a supported asset immediately activates this vulnerability for every subsequent deposit. No special attacker capability is required beyond making a normal deposit call.

---

### Recommendation

1. Measure the actual received amount using a balance-before/balance-after check inside `depositAsset`, and use that measured amount — not `depositAmount` — to compute `rsethAmountToMint`:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = getRsETHAmountToMint(asset, actualReceived);
require(rsethAmountToMint >= minRSETHAmountExpected, "MinimumAmountToReceiveNotMet");
_mintRsETH(rsethAmountToMint);
```

2. Apply the same fix to all L2 pool `deposit(address token, ...)` functions (`RSETHPool`, `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`).

3. Alternatively, explicitly disallow fee-on-transfer tokens in the asset-addition governance path.

---

### Proof of Concept

**Setup:** A fee-on-transfer token `FOT` with a 2% transfer fee is added as a supported asset. rsETH price = FOT price = 1 ETH.

1. Alice calls `depositAsset(FOT, 100e18, 0, "")`.
2. `_beforeDeposit` computes `rsethAmountToMint = getRsETHAmountToMint(FOT, 100e18)` → **100 rsETH**.
3. `safeTransferFrom` transfers 100 FOT from Alice; due to the 2% fee, the pool receives only **98 FOT**.
4. `_mintRsETH(100e18)` mints **100 rsETH** to Alice.
5. The pool now holds 98 FOT worth of assets but has issued 100 rsETH — a **2 rsETH deficit**.
6. At scale (e.g., 100M FOT deposited), the deficit is **2M FOT**, and the last depositors cannot redeem their rsETH.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
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

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```
