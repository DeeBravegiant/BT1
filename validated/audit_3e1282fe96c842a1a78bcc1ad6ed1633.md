### Title
Fee-on-Transfer / Deflation Token Deposit Mints rsETH Based on Stated Amount, Not Actual Received Amount - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset()` computes the rsETH mint amount from the caller-supplied `depositAmount` parameter **before** the token transfer occurs, then mints that full amount regardless of how many tokens the contract actually received. If a fee-on-transfer (deflation) token is ever whitelisted as a supported asset, every depositor receives more rsETH than the value they contributed, directly diluting all existing rsETH holders and driving the protocol toward insolvency. The identical pattern is present in every pool-level `deposit(token, amount, ...)` function and in both wrapper contracts.

---

### Finding Description

In `LRTDepositPool.depositAsset()`:

```solidity
// contracts/LRTDepositPool.sol  lines 99-118
function depositAsset(
    address asset,
    uint256 depositAmount,
    uint256 minRSETHAmountExpected,
    string calldata referralId
) external nonReentrant whenNotPaused onlySupportedERC20Token(asset) {
    // rsethAmountToMint is derived from depositAmount (the stated value)
    uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

    // Actual tokens received may be < depositAmount for deflation tokens
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);

    // Mints based on depositAmount, not actual received balance
    _mintRsETH(rsethAmountToMint);
}
```

`_beforeDeposit` calls `getRsETHAmountToMint(asset, depositAmount)` using the caller-supplied `depositAmount`:

```solidity
// contracts/LRTDepositPool.sol  lines 665
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

For a deflation token with a 1% burn, a depositor who passes `depositAmount = 100e18` causes the contract to receive only `99e18` tokens, yet `_mintRsETH` issues rsETH priced against the full `100e18`. The protocol's asset backing is permanently short by 1% per deposit.

The same root cause exists in:

| File | Function | Minted token |
|---|---|---|
| `contracts/pools/RSETHPoolV3.sol` | `deposit(token, amount, ...)` | wrsETH |
| `contracts/pools/RSETHPoolV3ExternalBridge.sol` | `deposit(token, amount, ...)` | wrsETH |
| `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` | `deposit(token, amount, ...)` | wrsETH |
| `contracts/pools/RSETHPool.sol` | `deposit(token, amount, ...)` | wrsETH |
| `contracts/L2/RsETHTokenWrapper.sol` | `_deposit()` | wrsETH (1:1 mint) |
| `contracts/agETH/AGETHTokenWrapper.sol` | `_deposit()` | wrapped agETH (1:1 mint) |

---

### Impact Explanation

Every deposit of a deflation token mints more rsETH (or wrsETH) than the actual asset value received. Over time the total rsETH supply becomes unbacked, making the protocol insolvent. Existing rsETH holders are diluted because `getTotalAssetDeposits` and the oracle-based exchange rate both depend on actual on-chain balances, which are lower than the amount used to compute the mint. This is **protocol insolvency** (Critical).

---

### Likelihood Explanation

The protocol uses an admin-controlled whitelist (`onlySupportedERC20Token` / `onlySupportedToken` / `allowedTokens`). The vulnerability is triggered the moment any fee-on-transfer LST or bridged token variant is added to that whitelist — an action that can be taken in good faith without awareness of the deflation mechanic. Several real LSTs (e.g., stETH in rebase mode, certain bridged representations) have transfer-fee variants. Likelihood is **Low** (requires a specific token to be whitelisted) but the impact is immediate and irreversible once triggered.

---

### Recommendation

Use a balance-before / balance-after pattern to determine the actual received amount, and use that value for all subsequent calculations and minting:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to every pool `deposit(token, amount, ...)` function and both wrapper `_deposit()` functions. Alternatively, explicitly document and enforce (via a registry flag or a transfer-amount verification check) that deflation/fee-on-transfer tokens are not supported.

---

### Proof of Concept

1. Admin whitelists a token `FeeToken` that burns 2% on every transfer.
2. Attacker calls `LRTDepositPool.depositAsset(FeeToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` using `depositAmount = 1000e18`.
4. `safeTransferFrom` executes; contract receives only `980e18` (2% burned).
5. `_mintRsETH` mints rsETH valued at `1000e18` worth of `FeeToken`.
6. Protocol holds `980e18` tokens but has issued rsETH backed by `1000e18` — a 2% shortfall per deposit.
7. Repeated deposits compound the insolvency; rsETH redemption value falls below 1:1. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L125-132)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, _to, _amount);
    }
```
