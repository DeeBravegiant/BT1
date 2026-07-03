### Title
Fee-on-Transfer Token Support Causes rsETH Over-Minting and Protocol Insolvency - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset` computes the rsETH mint amount from the caller-supplied `depositAmount` before the `safeTransferFrom` call. If the deposited asset is a fee-on-transfer token, the contract receives fewer tokens than `depositAmount`, but mints rsETH as if the full `depositAmount` arrived. This inflates rsETH supply beyond the actual asset backing, causing protocol insolvency.

### Finding Description
In `depositAsset`, the mint amount is pre-computed from the user-supplied `depositAmount`:

```solidity
// contracts/LRTDepositPool.sol L111
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// L114 — actual received amount may be less than depositAmount for fee-on-transfer tokens
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);

// L115 — mints based on depositAmount, not actual received balance
_mintRsETH(rsethAmountToMint);
```

`_beforeDeposit` delegates to `getRsETHAmountToMint`:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`amount` here is the caller-controlled `depositAmount`, not the balance delta. For a fee-on-transfer token with a 1% transfer fee, the contract receives `depositAmount * 0.99` but mints rsETH for `depositAmount * 1.00`. The 1% excess rsETH is unbacked.

The same pattern is present in all L2 pool variants:
- `RSETHPoolV3.deposit(address,uint256,string)` — L284 `safeTransferFrom`, L286 `viewSwapRsETHAmountAndFee(amount, token)` uses original `amount`
- `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)` — L403/L405 same pattern
- `RSETHPoolV3WithNativeChainBridge.deposit(address,uint256,string)` — L320/L322 same pattern
- `RsETHTokenWrapper._deposit` — L137 `safeTransferFrom(_amount)`, L139 `_mint(_to, _amount)` uses original `_amount`

### Impact Explanation
Every deposit with a fee-on-transfer asset mints more rsETH than the actual ETH-equivalent value received. The rsETH price is computed as `totalETHInProtocol / rsethSupply` in `LRTOracle._updateRsETHPrice`. Over-minted rsETH inflates the denominator without a corresponding increase in the numerator, depressing the rsETH price for all holders. At redemption time, the protocol cannot cover all outstanding rsETH with real assets — **protocol insolvency**. This is a Critical impact.

### Likelihood Explanation
The `onlySupportedERC20Token` modifier only checks whether the token is in the supported list; it does not screen for fee-on-transfer behavior. The protocol is explicitly designed to be extensible (governance can add new LSTs). Any future addition of a fee-on-transfer LST or yield-bearing token (e.g., a rebasing token with a transfer tax) immediately activates this path. The attacker entry point is the public `depositAsset` function — no privilege required once a fee-on-transfer token is listed.

### Recommendation
Measure the actual balance received by comparing the contract's balance before and after `safeTransferFrom`, and use the delta to compute the rsETH mint amount:

```solidity
function depositAsset(...) external ... {
    uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
    uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

    uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);

    emit AssetDeposit(msg.sender, asset, actualReceived, rsethAmountToMint, referralId);
}
```

Apply the same fix to all L2 pool `deposit(address,uint256,string)` functions and `RsETHTokenWrapper._deposit`.

### Proof of Concept

Assume a fee-on-transfer LST with a 1% transfer fee is added as a supported asset. The rsETH price is 1.05 ETH/rsETH and the LST price is 1.0 ETH.

1. Attacker calls `depositAsset(feeToken, 1000e18, 0, "")`.
2. `_beforeDeposit` computes `rsethAmountToMint = (1000e18 * 1e18) / 1.05e18 ≈ 952.38 rsETH` based on `depositAmount = 1000e18`.
3. `safeTransferFrom` transfers 1000 tokens; contract receives only 990 tokens (1% fee deducted by the token).
4. `_mintRsETH(952.38 rsETH)` mints rsETH backed by only 990 tokens (≈ 990 ETH equivalent).
5. The 9.52 rsETH excess is unbacked. Repeated across many depositors, the cumulative shortfall grows until the protocol cannot honor all redemptions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L390-412)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
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
