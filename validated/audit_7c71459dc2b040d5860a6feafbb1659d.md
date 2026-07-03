### Title
Fee-on-Transfer Token Support Causes rsETH Over-Minting and Protocol Insolvency - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset` calculates the rsETH amount to mint using the caller-supplied `depositAmount` before the actual token transfer occurs. If a fee-on-transfer ERC20 is ever added as a supported LST, the contract receives fewer tokens than `depositAmount` but mints rsETH as if the full amount arrived, permanently inflating rsETH supply relative to backing assets.

### Finding Description
In `LRTDepositPool.depositAsset`, the execution order is:

1. `_beforeDeposit(asset, depositAmount, ...)` computes `rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount)` using the nominal `depositAmount`.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` executes the transfer — for a fee-on-transfer token, the contract receives `depositAmount − fee`.
3. `_mintRsETH(rsethAmountToMint)` mints rsETH calculated from the full nominal `depositAmount`. [1](#0-0) 

The `_beforeDeposit` helper only reads `depositAmount` as a pure input; it never measures the actual balance change: [2](#0-1) 

The same pattern is replicated across every L2 pool variant that accepts ERC20 tokens:

- `RSETHPoolV3.deposit(address token, ...)` — `safeTransferFrom(amount)` then `viewSwapRsETHAmountAndFee(amount, token)` [3](#0-2) 
- `RSETHPoolV3ExternalBridge.deposit(address token, ...)` — identical pattern [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.deposit(address token, ...)` — identical pattern [5](#0-4) 
- `RSETHPoolNoWrapper.deposit(address token, ...)` — identical pattern [6](#0-5) 
- `RsETHTokenWrapper._deposit` — mints wrsETH 1:1 with `_amount` after `safeTransferFrom` [7](#0-6) 

### Impact Explanation
Every deposit with a fee-on-transfer token mints rsETH (or wrsETH) backed by fewer assets than the minted amount implies. The `LRTOracle` will compute an rsETH price based on actual on-chain balances, which are lower than the total rsETH supply warrants. This is a direct path to **protocol insolvency**: later redeemers cannot be made whole because the backing pool is systematically short. The shortfall compounds with every deposit. Impact: **Critical — protocol insolvency**.

### Likelihood Explanation
The currently deployed supported LSTs (stETH, cbETH, rETH, ETHx, sfrxETH, swETH) do not charge transfer fees today. However, the protocol's `LRTConfig` allows governance to add new supported assets at any time with no code-level guard against fee-on-transfer behavior. USDT, for example, has a fee mechanism that is currently set to zero but can be activated. Any future token addition that carries a transfer fee — even inadvertently — triggers the insolvency path. Likelihood: **Low** (requires a fee-on-transfer token to be added), but the absence of any defensive check makes the risk persistent.

### Recommendation
Measure the actual balance received rather than trusting the caller-supplied amount. Use a before/after balance check pattern:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to all L2 pool `deposit(address token, ...)` functions and `RsETHTokenWrapper._deposit`. Additionally, document that fee-on-transfer tokens are not supported and add an explicit check or allowlist validation when new assets are added via `LRTConfig`.

### Proof of Concept
1. Governance adds a token `FeeToken` (1% transfer fee) as a supported LST via `LRTConfig`.
2. Alice calls `LRTDepositPool.depositAsset(FeeToken, 1000e18, minRSETH, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `1000e18`.
4. `safeTransferFrom` transfers `1000e18` from Alice; `FeeToken` deducts 1%, so the contract receives `990e18`.
5. `_mintRsETH` mints rsETH equivalent to `1000e18` worth of `FeeToken`.
6. The protocol now holds `990e18` tokens but has issued rsETH backed by `1000e18` — a 1% insolvency gap per deposit.
7. Repeated deposits widen the gap. The last redeemers cannot withdraw their full rsETH value.

### Citations

**File:** contracts/LRTDepositPool.sol (L110-117)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
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

**File:** contracts/pools/RSETHPoolV3.sol (L282-292)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L401-411)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L318-328)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L260-270)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
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
